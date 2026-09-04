"""Benchmark index history from the NSE daily index archive.

Same shape as the bhavcopy source: one immutable file per trading day covering
every NSE index, cached on disk. This is what supplies the Nifty 500 close used
for relative strength — Yahoo's ^CRSLDX would do the same job but rate-limits
within seconds of a burst, and this is the exchange's own file.

The file also carries index-level P/E, P/B and dividend yield, which the value
pillar will want in phase 4 as a market-wide reference point.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

import httpx

from ..config import DATA_DIR, settings

ARCHIVE_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"
CACHE_DIR = DATA_DIR / "cache" / "indices"

BENCHMARK = "Nifty 500"

# Stored for market context alongside the benchmark. A screener that can only
# see its own universe cannot tell you whether a stock is falling because the
# business is failing or because the whole market is — and India VIX is the
# cheapest read on whether the tape is calm or frightened.
TRACKED_INDICES = (
    BENCHMARK,
    "Nifty 50",
    "Nifty Bank",
    "Nifty Midcap 150",
    "Nifty Smallcap 250",
    "Nifty IT",
    "Nifty Auto",
    "Nifty Pharma",
    "Nifty FMCG",
    "Nifty Metal",
    "Nifty Realty",
    "India VIX",
)

REQUIRED_COLUMNS = {
    "Index Name", "Index Date", "Open Index Value", "High Index Value",
    "Low Index Value", "Closing Index Value",
}
MIN_EXPECTED_INDICES = 50


class IndexArchiveError(RuntimeError):
    pass


class IndexArchiveUnavailable(IndexArchiveError):
    """No file for this date — weekend, holiday, or not yet published."""


@dataclass(frozen=True)
class IndexQuote:
    index_name: str
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None
    turnover_cr: float | None
    pe: float | None
    pb: float | None
    div_yield: float | None


def _cache_path(on: date):
    return CACHE_DIR / f"{on:%Y%m%d}.csv"


def _miss_path(on: date):
    """Marker for a non-trading day; see bhavcopy._miss_path."""
    return CACHE_DIR / f"{on:%Y%m%d}.nosession"


def _number(raw: str | None) -> float | None:
    """NSE writes '-' for not-applicable and '.34' for 0.34."""
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fetch_raw(client: httpx.Client, on: date, *, use_cache: bool = True) -> str:
    cached = _cache_path(on)
    if use_cache and cached.exists():
        return cached.read_text()
    if use_cache and _miss_path(on).exists():
        raise IndexArchiveUnavailable(f"no index archive for {on} (cached miss)")

    response = client.get(ARCHIVE_URL.format(ddmmyyyy=f"{on:%d%m%Y}"))
    if response.status_code == 404:
        _miss_path(on).parent.mkdir(parents=True, exist_ok=True)
        _miss_path(on).touch()
        raise IndexArchiveUnavailable(f"no index archive for {on}")
    response.raise_for_status()

    text = response.text
    if "Index Name" not in text[:2000]:
        raise IndexArchiveError(f"{on}: response is not the index archive")

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(text)
    return text


def parse(
    text: str, *, only: str | None = None, keep: tuple[str, ...] = TRACKED_INDICES
) -> dict[str, IndexQuote]:
    reader = csv.DictReader(io.StringIO(text))
    columns = {c.strip() for c in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise IndexArchiveError(f"index archive is missing columns: {sorted(missing)}")

    quotes: dict[str, IndexQuote] = {}
    seen = 0
    for row in reader:
        name = (row.get("Index Name") or "").strip()
        if not name:
            continue
        seen += 1
        if only is not None and name != only:
            continue
        if only is None and keep is not None and name not in keep:
            continue

        close = _number(row.get("Closing Index Value"))
        if close is None:
            continue

        quotes[name] = IndexQuote(
            index_name=name,
            date=_parse_date(row["Index Date"]),
            open=_number(row.get("Open Index Value")),
            high=_number(row.get("High Index Value")),
            low=_number(row.get("Low Index Value")),
            close=close,
            volume=int(_number(row.get("Volume")) or 0) or None,
            turnover_cr=_number(row.get("Turnover (Rs. Cr.)")),
            pe=_number(row.get("P/E")),
            pb=_number(row.get("P/B")),
            div_yield=_number(row.get("Div Yield")),
        )

    if seen < MIN_EXPECTED_INDICES:
        raise IndexArchiveError(
            f"only {seen} indices in the file, expected at least "
            f"{MIN_EXPECTED_INDICES} — the layout probably changed"
        )
    required = only or BENCHMARK
    if required not in quotes:
        raise IndexArchiveError(f"benchmark {required!r} not present in the file")

    return quotes


def _parse_date(raw: str) -> date:
    text = raw.strip()
    for fmt in ("%d-%m-%Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            from datetime import datetime

            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise IndexArchiveError(f"unparsable index date {raw!r}")


def fetch(client: httpx.Client, on: date, *, use_cache: bool = True) -> IndexQuote | None:
    """The benchmark only — kept for callers that just need relative strength."""
    return parse(fetch_raw(client, on, use_cache=use_cache)).get(BENCHMARK)


def fetch_all(client: httpx.Client, on: date, *, use_cache: bool = True) -> list[IndexQuote]:
    """Every tracked index for a date."""
    return list(parse(fetch_raw(client, on, use_cache=use_cache)).values())


def to_row(quote: IndexQuote) -> dict:
    return {
        "index_name": quote.index_name,
        "is_benchmark": quote.index_name == BENCHMARK,
        "date": quote.date.isoformat(),
        "open": quote.open,
        "high": quote.high,
        "low": quote.low,
        "close": quote.close,
        "volume": quote.volume,
        "turnover_cr": quote.turnover_cr,
        "pe": quote.pe,
        "pb": quote.pb,
        "div_yield": quote.div_yield,
    }
