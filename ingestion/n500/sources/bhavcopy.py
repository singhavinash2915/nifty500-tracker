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
from datetime import date, datetime, timedelta

import httpx

from ..config import DATA_DIR, settings

ARCHIVE_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)

# NSE replaced the bhavcopy with the UDiFF layout partway through; the new URL
# 404s for anything before this date and the old one is still served for
# anything after it, so the cut is a hard switch rather than a fallback.
UDIFF_FROM = date(2024, 1, 1)

LEGACY_URL = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/"
    "{year}/{month}/cm{day}{month}{year}bhav.csv.zip"
)

# The old file names the columns differently and says less. What it does not
# have that UDiFF does: FinInstrmTp, so index and stock derivatives cannot be
# excluded by type — they are not in the cash file at all, so SERIES alone is
# enough. And its PREVCLOSE is the raw previous close, *not* adjusted for a
# corporate action: TATASTEEL's 1:10 split on 28 July 2022 opened at 98.1
# against a PREVCLOSE of 959.4. That would matter if the adjustment read that
# column, but `_action_ratio` measures the gap on the open against the previous
# close, so both layouts feed it the same two numbers.
LEGACY_COLUMNS = {
    "SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE",
    "PREVCLOSE", "TOTTRDQTY", "TIMESTAMP", "ISIN",
}

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

# Inside this band the observed gap is close enough to a candidate that the
# match is evidence by itself, and the nearest one is taken. Outside it, the
# day's own move swamps the difference between candidates and simplicity
# decides instead. See `_snap`.
EXACT_TOLERANCE = 0.005

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


def _is_legacy(on: date) -> bool:
    return on < UDIFF_FROM


def _archive_url(on: date) -> str:
    if not _is_legacy(on):
        return ARCHIVE_URL.format(yyyymmdd=f"{on:%Y%m%d}")
    return LEGACY_URL.format(
        year=f"{on:%Y}", month=f"{on:%b}".upper(), day=f"{on:%d}"
    )


def _cache_path(on: date):
    return CACHE_DIR / f"{on:%Y%m%d}.csv"


def _miss_path(on: date):
    """Marker for 'the exchange did not trade that day'.

    Negative results are cached too, because the candidate list now includes
    weekends (NSE runs a special Budget-day session on 1 February even when it
    falls on a Saturday) and re-fetching a few hundred certain 404s on every
    run would be the slowest part of the job.

    A miss is only permanent once the day is over. See `_miss_is_settled`.
    """
    return CACHE_DIR / f"{on:%Y%m%d}.nosession"


def _miss_is_settled(on: date) -> bool:
    """Whether a cached 404 for `on` can still be believed.

    Not every 404 means the same thing. For a Sunday two years ago it means the
    exchange did not trade and never will; for this afternoon it means the file
    has not been published yet. Both were being cached identically, so a probe
    run at 09:11 on a trading morning wrote a permanent "no session" marker for
    that day and the pipeline would have sat one day behind for good — silently,
    since a skipped date looks exactly like a holiday in the logs.

    A miss is trusted only if it was recorded after the day it describes had
    finished. Anything recorded during or before that day is re-checked.
    """
    path = _miss_path(on)
    if not path.exists():
        return False
    recorded = datetime.fromtimestamp(path.stat().st_mtime)
    settled_from = datetime.combine(on, datetime.min.time()) + timedelta(days=1)
    return recorded >= settled_from


def fetch_raw(client: httpx.Client, on: date, *, use_cache: bool = True) -> str:
    """Return the bhavcopy CSV text for a date, caching it on disk.

    The archive is immutable once published, so a cached file is always as good
    as a fresh fetch — and a backfill re-run then costs nothing.
    """
    cached = _cache_path(on)
    if use_cache and cached.exists():
        return cached.read_text()
    if use_cache and _miss_is_settled(on):
        raise BhavcopyUnavailable(f"no bhavcopy for {on} (cached miss)")

    response = client.get(_archive_url(on))
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
    """Parse to {symbol: Quote}, keeping only cash-segment equity series.

    Dispatches on the layout rather than the date, so a cached file parses the
    same way whenever it is read back.
    """
    header = {c.strip() for c in (text.splitlines() or [""])[0].split(",")}
    if "TckrSymb" not in header and LEGACY_COLUMNS <= header:
        return _parse_legacy(text, on=on)

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


def _parse_legacy(text: str, *, on: date | None = None) -> dict[str, Quote]:
    """The pre-2024 layout, mapped onto the same Quote."""
    quotes: dict[str, Quote] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("SERIES") or "").strip() not in EQUITY_SERIES:
            continue
        symbol = row["SYMBOL"].strip().upper()
        try:
            quote = Quote(
                symbol=symbol,
                # "02-JAN-2023" — the only field that needs real work.
                date=datetime.strptime(row["TIMESTAMP"].strip(), "%d-%b-%Y").date(),
                open=float(row["OPEN"]),
                high=float(row["HIGH"]),
                low=float(row["LOW"]),
                close=float(row["CLOSE"]),
                prev_close=float(row["PREVCLOSE"]),
                volume=int(float(row["TOTTRDQTY"] or 0)),
                isin=(row.get("ISIN") or "").strip(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BhavcopyError(f"{symbol}: unparsable legacy row ({exc})") from exc

        if not (quote.low <= quote.close <= quote.high):
            raise BhavcopyError(
                f"{symbol} on {quote.date}: close {quote.close} outside "
                f"low/high {quote.low}/{quote.high}"
            )
        quotes[symbol] = quote

    if len(quotes) < MIN_EXPECTED_EQUITIES:
        raise BhavcopyError(
            f"only {len(quotes)} equities parsed from the legacy layout, "
            f"expected at least {MIN_EXPECTED_EQUITIES}"
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


def _complexity(ratio: float) -> int:
    """How baroque a corporate action has to be to produce this factor.

    A plain 1:10 split is the simplest thing that can halve-and-then-some; the
    compound ratios exist for genuine cases like BAJFINANCE's split-plus-bonus,
    but they should never win against a plain one that also fits.
    """
    from fractions import Fraction

    frac = Fraction(ratio).limit_denominator(1000)
    return frac.numerator + frac.denominator


def _snap(observed: float) -> float | None:
    """The corporate-action ratio behind an observed gap, or None if none fits.

    Nearest-wins is wrong here, and TATASTEEL's 1:10 split shows why. It opened
    at 98.10 against a previous close of 959.40 — an observed factor of 0.10225.
    The true 0.1 is 2.3% away, but 7/68 (a 1:4 split compounded with a 10:7
    bonus) is 0.7% away and won on nearness alone, restating history at 98.76
    instead of 95.94: a 2.9% error running back through every earlier bar.

    The reason nearness misleads is that the observed factor is not the ratio.
    It is the ratio times whatever the stock genuinely did on the open, which is
    routinely a percent or two. So a match at 2% and a match at 0.7% are not
    meaningfully different evidence, and among candidates that crowded the rarer
    fraction wins by coincidence about as often as the real one.

    Hence two stages. A match inside `EXACT_TOLERANCE` is strong evidence in its
    own right — the stock opened almost exactly where the action implies — so
    the nearest of those wins. Only when nothing is that close does the wider
    band open up, and there the simplest fraction wins: a 1:10 split is a common
    event and a 1:4-with-10:7-bonus is not.
    """
    exact = [r for r in RATIOS if abs(observed / r - 1.0) <= EXACT_TOLERANCE]
    if exact:
        return min(exact, key=lambda r: abs(observed / r - 1.0))

    fitting = [r for r in RATIOS if abs(observed / r - 1.0) <= SNAP_TOLERANCE]
    if not fitting:
        return None
    return min(fitting, key=lambda r: (_complexity(r), abs(observed / r - 1.0)))


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
