"""Daily OHLCV from the official NSE bhavcopy archive.

Chosen over the Yahoo chart endpoint as the primary price source for one
structural reason: a bhavcopy is *one request per trading day for the entire
market*, where Yahoo is one request per symbol. Backfilling two years costs
~500 requests here versus ~500 requests per sweep there, and Yahoo starts
returning 429 within seconds of a burst. It is also the exchange's own file,
so there is no terms-of-service question.

Corporate actions
-----------------
Bhavcopy prices are unadjusted, which would make a 1:2 split look like a 50%
crash and poison every momentum figure. NSE does however publish
`PrvsClsgPric` already adjusted for the action. So the adjustment factor for
day t is

    factor(t) = PrvsClsgPric(t) / ClsPric(t-1)

which is 1.0 on ordinary days and 0.5 across a 1:2 split. `adjust()` walks
those factors backwards to restate history on today's basis. No second data
source is needed.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date

import httpx

from ..config import DATA_DIR, settings

ARCHIVE_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)

CACHE_DIR = DATA_DIR / "cache" / "bhavcopy"

REQUIRED_COLUMNS = {
    "TradDt", "TckrSymb", "SctySrs", "FinInstrmTp", "ISIN",
    "OpnPric", "HghPric", "LwPric", "ClsPric", "PrvsClsgPric", "TtlTradgVol",
}

# A real session lists a couple of thousand cash-segment equities.
MIN_EXPECTED_EQUITIES = 1200

# Series that are the ordinary equity under different settlement rules. BE and
# BZ are the surveillance ("trade for trade") buckets: a stock moved there is
# still the same stock, and filtering it out punches a hole in the history that
# later reads as a corporate action. ITI sat in BE for 61 straight sessions.
EQUITY_SERIES = frozenset({"EQ", "BE", "BZ"})

# Overnight moves outside this band are candidate corporate actions. The
# magnitude threshold is the actual detector — snapping only refines the
# factor afterwards. 0.78 catches a 1:3 bonus (0.75) with margin while staying
# well clear of ordinary daily volatility.
ACTION_FALL_BELOW = 0.78
ACTION_RISE_ABOVE = 1.28

# How far the observed ratio may sit from the nearest plausible one. The
# ex-date price also moves on its own merits, so this cannot be tiny; 2.01%
# was the worst error across the 56 real actions in two years of history.
SNAP_TOLERANCE = 0.03

# Only adjacent sessions are comparable. Across a suspension or a symbol
# change the two closes are unrelated and any ratio between them is noise.
MAX_SESSION_GAP_DAYS = 5


class BhavcopyError(RuntimeError):
    pass


class BhavcopyUnavailable(BhavcopyError):
    """No file for this date — a weekend, a holiday, or not yet published."""


@dataclass(frozen=True)
class Quote:
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    prev_close: float
    volume: int
    isin: str


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=settings.request_timeout,
        follow_redirects=True,
    )


def _cache_path(on: date):
    return CACHE_DIR / f"{on:%Y%m%d}.csv"


def _miss_path(on: date):
    """Marker for 'the exchange did not trade that day'.

    Negative results are cached too, because the candidate list now includes
    weekends (NSE runs a special Budget-day session on 1 February even when it
    falls on a Saturday) and re-fetching a few hundred certain 404s on every
    run would be the slowest part of the job.
    """
    return CACHE_DIR / f"{on:%Y%m%d}.nosession"


def fetch_raw(client: httpx.Client, on: date, *, use_cache: bool = True) -> str:
    """Return the bhavcopy CSV text for a date, caching it on disk.

    The archive is immutable once published, so a cached file is always as good
    as a fresh fetch — and a backfill re-run then costs nothing.
    """
    cached = _cache_path(on)
    if use_cache and cached.exists():
        return cached.read_text()
    if use_cache and _miss_path(on).exists():
        raise BhavcopyUnavailable(f"no bhavcopy for {on} (cached miss)")

    response = client.get(ARCHIVE_URL.format(yyyymmdd=f"{on:%Y%m%d}"))
    if response.status_code == 404:
        _miss_path(on).parent.mkdir(parents=True, exist_ok=True)
        _miss_path(on).touch()
        raise BhavcopyUnavailable(f"no bhavcopy for {on}")
    response.raise_for_status()

    if not response.content.startswith(b"PK"):
        raise BhavcopyError(f"{on}: response was not a zip archive")

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    if len(names) != 1:
        raise BhavcopyError(f"{on}: expected one file in the zip, got {names}")
    text = archive.read(names[0]).decode("utf-8")

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(text)
    return text


def parse(text: str, *, on: date | None = None) -> dict[str, Quote]:
    """Parse to {symbol: Quote}, keeping only cash-segment equity series."""
    reader = csv.DictReader(io.StringIO(text))
    columns = {c.strip() for c in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise BhavcopyError(f"bhavcopy is missing columns: {sorted(missing)}")

    quotes: dict[str, Quote] = {}
    for row in reader:
        # EQUITY_SERIES excludes SME, ETFs and government bonds; STK excludes
        # index and stock derivatives.
        if row["SctySrs"] not in EQUITY_SERIES or row["FinInstrmTp"] != "STK":
            continue

        symbol = row["TckrSymb"].strip().upper()
        try:
            quote = Quote(
                symbol=symbol,
                date=date.fromisoformat(row["TradDt"].strip()),
                open=float(row["OpnPric"]),
                high=float(row["HghPric"]),
                low=float(row["LwPric"]),
                close=float(row["ClsPric"]),
                prev_close=float(row["PrvsClsgPric"]),
                volume=int(float(row["TtlTradgVol"] or 0)),
                isin=row["ISIN"].strip(),
            )
        except (TypeError, ValueError) as exc:
            raise BhavcopyError(f"{symbol}: unparsable row ({exc})") from exc

        if not (quote.low <= quote.close <= quote.high):
            raise BhavcopyError(
                f"{symbol} on {quote.date}: close {quote.close} outside "
                f"low/high {quote.low}/{quote.high}"
            )
        quotes[symbol] = quote

    if len(quotes) < MIN_EXPECTED_EQUITIES:
        raise BhavcopyError(
            f"only {len(quotes)} equities parsed, expected at least "
            f"{MIN_EXPECTED_EQUITIES} — the file layout probably changed"
        )

    if on is not None:
        stamped = next(iter(quotes.values())).date
        if stamped != on:
            raise BhavcopyError(f"asked for {on} but the file is stamped {stamped}")

    return quotes


def fetch(client: httpx.Client, on: date, *, use_cache: bool = True) -> dict[str, Quote]:
    return parse(fetch_raw(client, on, use_cache=use_cache), on=on)


# Face-value splits actually used on NSE.
SPLIT_RATIOS = (1 / 2, 2 / 5, 1 / 4, 1 / 5, 1 / 10, 1 / 20, 1 / 25, 1 / 50, 1 / 100)


def _plausible_ratios() -> list[float]:
    """Price factors a real corporate action can produce.

    A bonus of a:b (a free shares per b held) multiplies the price by
    b / (a + b). A split from face value x to y multiplies it by y / x. Events
    are sometimes combined — BAJFINANCE ran a 1:2 split with a 4:1 bonus, for
    a compound factor of 0.1 — so products of the two are included.

    Restricting to this set rather than "any fraction p/q" matters: with
    denominators up to 20 the number line is so crowded that every ratio finds
    a match within 3%, which would make the snap test meaningless.
    """
    from math import gcd

    bonus = {
        b / (a + b)
        for a in range(1, 11)
        for b in range(1, 11)
        if gcd(a, b) == 1
    }
    combined = {split * bonus_factor for split in SPLIT_RATIOS for bonus_factor in bonus}
    ratios = {1.0} | set(SPLIT_RATIOS) | bonus | combined
    ratios |= {1.0 / r for r in ratios if r > 0}      # reverse splits
    return sorted(r for r in ratios if 0.005 <= r <= 200.0)


RATIOS = _plausible_ratios()


def _snap(observed: float) -> float | None:
    """Nearest simple corporate-action ratio, or None if nothing fits."""
    best = min(RATIOS, key=lambda candidate: abs(observed / candidate - 1.0))
    return best if abs(observed / best - 1.0) <= SNAP_TOLERANCE else None


def _action_ratio(previous: Quote, current: Quote) -> float | None:
    """The corporate-action factor entering `current`, or None if there isn't one.

    Measured on the open: an ex-date opens at the adjusted price, whereas a
    crash is capped at the 10% circuit on the open and does its falling during
    the session.
    """
    if previous.close <= 0 or current.close <= 0:
        return None
    if (current.date - previous.date).days > MAX_SESSION_GAP_DAYS:
        return None

    # Some illiquid sessions report no open; the close is the only fallback,
    # and it is why the threshold is kept well clear of the circuit limit.
    reference = current.open if current.open and current.open > 0 else current.close
    observed = reference / previous.close
    if ACTION_FALL_BELOW < observed < ACTION_RISE_ABOVE:
        return None
    return _snap(observed)


def adjust(quotes: list[Quote]) -> list[dict]:
    """Restate a single symbol's history on the latest basis.

    `quotes` must be one symbol, ascending by date. Returns price rows carrying
    both the raw close and an `adj_close` corrected for splits and bonuses.
    """
    if not quotes:
        return []

    ordered = sorted(quotes, key=lambda q: q.date)

    # factors[i] is the corporate-action ratio applied *entering* bar i.
    factors = [1.0] * len(ordered)
    for i in range(1, len(ordered)):
        ratio = _action_ratio(ordered[i - 1], ordered[i])
        if ratio is not None:
            factors[i] = ratio

    # A bar is scaled by the product of every factor that comes after it.
    cumulative = [1.0] * len(ordered)
    running = 1.0
    for i in range(len(ordered) - 1, -1, -1):
        cumulative[i] = running
        running *= factors[i]

    rows: list[dict] = []
    for quote, factor in zip(ordered, cumulative):
        rows.append(
            {
                "symbol": quote.symbol,
                "date": quote.date.isoformat(),
                "open": quote.open,
                "high": quote.high,
                "low": quote.low,
                "close": quote.close,
                "adj_close": round(quote.close * factor, 6),
                "volume": quote.volume,
            }
        )
    return rows


def corporate_actions(quotes: list[Quote]) -> list[tuple[date, float]]:
    """Detected split/bonus dates with their ratio. For auditing."""
    ordered = sorted(quotes, key=lambda q: q.date)
    found = []
    for i in range(1, len(ordered)):
        ratio = _action_ratio(ordered[i - 1], ordered[i])
        if ratio is not None:
            found.append((ordered[i].date, round(ratio, 6)))
    return found


def unexplained_moves(quotes: list[Quote]) -> list[tuple[date, float]]:
    """Large close-to-close moves that were *not* treated as corporate actions.

    These are the genuine crashes and melt-ups — a stock that opened near the
    circuit and then collapsed. Worth surfacing so a real event is visible in
    the audit trail rather than being mistaken for a data fault.
    """
    ordered = sorted(quotes, key=lambda q: q.date)
    found = []
    for i in range(1, len(ordered)):
        previous, current = ordered[i - 1], ordered[i]
        if previous.close <= 0 or current.close <= 0:
            continue
        if (current.date - previous.date).days > MAX_SESSION_GAP_DAYS:
            continue
        if _action_ratio(previous, current) is not None:
            continue
        observed = current.close / previous.close
        if observed <= ACTION_FALL_BELOW or observed >= ACTION_RISE_ABOVE:
            found.append((current.date, round(observed, 6)))
    return found
