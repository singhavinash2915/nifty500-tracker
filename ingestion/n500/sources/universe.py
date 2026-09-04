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
import time
from dataclasses import dataclass, asdict
from datetime import date, timedelta

import httpx

from ..config import settings

INDEX_CSV_URL = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"

EXPECTED_HEADER = ["Company Name", "Industry", "Symbol", "Series", "ISIN Code"]
MIN_EXPECTED_ROWS = 450          # a real list is 500; below this something broke
MAX_EXPECTED_ROWS = 520
ISIN_RE = re.compile(r"^IN[A-Z0-9]{10}$")


# Exchange-traded funds worth watching alongside the index constituents.
#
# They are ordinary EQ/STK rows in the bhavcopy, so prices, technicals and
# support zones all work unchanged. They have no financial statements, so the
# quality and value pillars stay null and the blend falls back to the technical
# — which is the honest answer for an instrument that has no revenue.
#
# Chosen to cover the asset classes a Nifty 500 screener cannot otherwise see:
# gold and silver, the large- and mid-cap indices in tradeable form, and a
# little offshore equity.
TRACKED_ETFS: dict[str, str] = {
    "GOLDBEES": "Nippon India ETF Gold BeES",
    "SILVERBEES": "Nippon India Silver ETF",
    "NIFTYBEES": "Nippon India ETF Nifty 50 BeES",
    "BANKBEES": "Nippon India ETF Nifty Bank BeES",
    "JUNIORBEES": "Nippon India ETF Nifty Next 50 Junior BeES",
    "ITBEES": "Nippon India ETF Nifty IT",
    "MON100": "Motilal Oswal Nasdaq 100 ETF",
    "HNGSNGBEES": "Nippon India ETF Hang Seng BeES",
}

# LIQUIDBEES was tracked briefly and removed. It is a cash-equivalent that sits
# near 1000 and barely moves, so the volatility guard rewarded it for going
# nowhere and it scored into the top decile — a momentum screener has nothing
# useful to say about a money-market proxy, and leaving it in would put a
# parking instrument at the top of a list of investment candidates.


@dataclass(frozen=True)
class Constituent:
    symbol: str
    company_name: str
    sector: str
    industry: str
    isin: str
    series: str
    instrument_type: str = "equity"


class UniverseParseError(RuntimeError):
    """Raised when the CSV no longer looks like what we expect."""


# One request against a public CSV, retried because a single read timeout at
# 19:15 should not be the reason a whole night's data is missing. Backoff is
# short: the file is 33KB and either the host answers or it does not.
FETCH_ATTEMPTS = 3
FETCH_BACKOFF_SECONDS = 5.0


def fetch_csv(url: str = INDEX_CSV_URL, *, attempts: int = FETCH_ATTEMPTS) -> str:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": settings.user_agent},
                timeout=settings.request_timeout,
                follow_redirects=True,
            )
            response.raise_for_status()
            return response.text
        except (httpx.HTTPError, httpx.StreamError) as exc:
            last = exc
            if attempt < attempts:
                time.sleep(FETCH_BACKOFF_SECONDS * attempt)
    raise last if last else RuntimeError("fetch failed with no exception")


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


def etf_constituents() -> list[Constituent]:
    """The tracked ETFs, shaped like constituents so one loader handles both."""
    return [
        Constituent(
            symbol=symbol,
            company_name=name,
            # Their own bucket: ranking a gold ETF against equities on any
            # sector-relative metric would be comparing unlike things.
            sector="Exchange Traded Funds",
            industry="Exchange Traded Funds",
            isin="",
            series="EQ",
            instrument_type="etf",
        )
        for symbol, name in sorted(TRACKED_ETFS.items())
    ]


def to_stock_row(item: Constituent, *, today: date) -> dict:
    row = asdict(item)
    row.pop("isin") if not row.get("isin") else None
    row["last_seen_on"] = today.isoformat()
    row["is_active"] = True
    row["updated_at"] = f"{today.isoformat()}T00:00:00+00:00"
    return row
