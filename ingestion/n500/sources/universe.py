"""Nifty 500 constituent list, from the official NSE indices CSV.

The file is small, public and stable in shape, so this is the one source in the
pipeline that needs no scraping etiquette beyond a sane user agent. Parser
sanity assertions still run: if NSE changes the layout we want a loud failure,
not 500 rows of nulls.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, asdict
from datetime import date, timedelta

import httpx

from ..config import settings

INDEX_CSV_URL = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"

EXPECTED_HEADER = ["Company Name", "Industry", "Symbol", "Series", "ISIN Code"]
MIN_EXPECTED_ROWS = 450          # a real list is 500; below this something broke
MAX_EXPECTED_ROWS = 520
ISIN_RE = re.compile(r"^IN[A-Z0-9]{10}$")


@dataclass(frozen=True)
class Constituent:
    symbol: str
    company_name: str
    sector: str
    industry: str
    isin: str
    series: str


class UniverseParseError(RuntimeError):
    """Raised when the CSV no longer looks like what we expect."""


def fetch_csv(url: str = INDEX_CSV_URL) -> str:
    response = httpx.get(
        url,
        headers={"User-Agent": settings.user_agent},
        timeout=settings.request_timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def parse_csv(text: str) -> list[Constituent]:
    reader = csv.DictReader(io.StringIO(text))

    header = [h.strip() for h in (reader.fieldnames or [])]
    if header != EXPECTED_HEADER:
        raise UniverseParseError(
            f"unexpected header {header!r}, expected {EXPECTED_HEADER!r}"
        )

    constituents: list[Constituent] = []
    seen: set[str] = set()

    for line_no, row in enumerate(reader, start=2):
        symbol = (row.get("Symbol") or "").strip().upper()
        name = (row.get("Company Name") or "").strip()
        industry = (row.get("Industry") or "").strip()
        isin = (row.get("ISIN Code") or "").strip().upper()
        series = (row.get("Series") or "").strip().upper()

        if not symbol:
            raise UniverseParseError(f"line {line_no}: missing symbol")
        if not name:
            raise UniverseParseError(f"line {line_no}: {symbol} has no company name")
        if not industry:
            raise UniverseParseError(f"line {line_no}: {symbol} has no industry")
        if not ISIN_RE.match(isin):
            raise UniverseParseError(f"line {line_no}: {symbol} has bad ISIN {isin!r}")
        if symbol in seen:
            raise UniverseParseError(f"line {line_no}: duplicate symbol {symbol}")
        seen.add(symbol)

        constituents.append(
            Constituent(
                symbol=symbol,
                company_name=name,
                # NSE's "Industry" column is its macro sector (e.g. "Financial
                # Services"). That is the right granularity for peer-relative
                # ranking; a finer industry label arrives with fundamentals.
                sector=industry,
                industry=industry,
                isin=isin,
                series=series,
            )
        )

    count = len(constituents)
    if not MIN_EXPECTED_ROWS <= count <= MAX_EXPECTED_ROWS:
        raise UniverseParseError(
            f"got {count} constituents, expected {MIN_EXPECTED_ROWS}-{MAX_EXPECTED_ROWS}"
        )

    return constituents


def fetch_constituents(url: str = INDEX_CSV_URL) -> list[Constituent]:
    return parse_csv(fetch_csv(url))


def week_start(on: date | None = None) -> date:
    """Monday of the given date's week — the key for membership snapshots."""
    on = on or date.today()
    return on - timedelta(days=on.weekday())


def to_stock_row(item: Constituent, *, today: date) -> dict:
    row = asdict(item)
    row["last_seen_on"] = today.isoformat()
    row["is_active"] = True
    row["updated_at"] = f"{today.isoformat()}T00:00:00+00:00"
    return row
