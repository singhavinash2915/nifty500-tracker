"""Nifty 500 membership as it stood on past dates.

Why this exists
---------------
Every backtest number in this project has been computed on *today's* index. A
stock joins the Nifty 500 because it grew into it, so testing today's list back
through 2022 means the sample was picked partly for having done well — the
2022 returns of a company added in 2025 are returns nobody could have chosen to
earn. It also silently drops everything that failed and left, which is the half
that would have hurt. Both push the same way: results come out too good.

The fix needs the constituent list as it stood at each rebalance, and NSE
publishes only the current one. The Internet Archive has snapshots of that same
CSV going back to 2018, which is the closest thing to a point-in-time record
available without paying for one.

The honest shape of what this gives you
---------------------------------------
Coverage is uneven. There are snapshots either side of 2018-19, then a six-year
gap, then several from 2024 on. So membership for a 2023 rebalance resolves to
the February 2019 list — stale by four years, and wrong in the sense that it
misses everything that joined in between.

Stale is not the same as biased, and that distinction is the whole point. A
list captured *before* the test period contains no information about what
happened during it, so using it cannot flatter the result; it can only make the
universe less like the one you would really have traded. Using today's list, by
contrast, is a straight look-ahead. Given the choice between a universe that is
out of date and one that knows the future, out of date is the one that produces
an honest number.

Refreshing the snapshot list
----------------------------
`TIMESTAMPS` is a captured result, not a guess — re-derive it with the CDX API
rather than inventing dates:

    http://web.archive.org/cdx/search/cdx
        ?url=niftyindices.com/IndexConstituent/ind_nifty500list.csv
        &output=json&fl=timestamp,statuscode

Only 200s carry a file; a 301 is the archive redirecting and yields nothing.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from ..config import DATA_DIR, settings

CACHE_DIR = DATA_DIR / "cache" / "membership"

WAYBACK_URL = (
    "https://web.archive.org/web/{timestamp}id_/"
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
)

# Snapshots that returned an actual CSV. See the module docstring for how to
# re-derive this; the gap between 2019 and 2024 is the archive's, not an
# omission here.
TIMESTAMPS = (
    "20181004105632",
    "20190201175107",
    "20240207233933",
    "20250616113621",
    "20250815235304",
    "20260107050129",
    "20260502114023",
    "20260827161651",
)

EXPECTED_HEADER = ["Company Name", "Industry", "Symbol", "Series", "ISIN Code"]

# The index is 500 by construction and the file occasionally carries a few
# extra rows around a rebalance. Anything far outside this is a fetch that
# returned a redirect stub or an error page rather than the list.
MIN_ROWS = 400
MAX_ROWS = 550


class MembershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class Constituent:
    symbol: str
    company_name: str
    industry: str
    isin: str


def snapshot_date(timestamp: str) -> date:
    return datetime.strptime(timestamp[:8], "%Y%m%d").date()


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.user_agent},
        timeout=max(settings.request_timeout, 120.0),
        follow_redirects=True,
    )


def _cache_path(timestamp: str):
    return CACHE_DIR / f"{timestamp}.csv"


def fetch_raw(client: httpx.Client, timestamp: str, *, use_cache: bool = True) -> str:
    """The archived CSV text, cached on disk.

    An archived capture never changes, so a cached copy is always as good as a
    fresh one — and the archive is slow and rate-limits, so re-running this job
    should not re-fetch.
    """
    cached = _cache_path(timestamp)
    if use_cache and cached.exists():
        return cached.read_text()

    response = client.get(WAYBACK_URL.format(timestamp=timestamp))
    response.raise_for_status()
    text = response.text

    # The archive answers a rate-limited or missing capture with an HTML page
    # and a 200, so the header is the only reliable check that this is the file.
    if not text.lstrip().startswith("Company Name"):
        raise MembershipError(
            f"{timestamp}: response is not the constituent CSV "
            f"(starts {text[:60]!r})"
        )

    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(text)
    return text


def parse(text: str) -> list[Constituent]:
    reader = csv.DictReader(io.StringIO(text))
    header = [c.strip() for c in (reader.fieldnames or [])]
    if header != EXPECTED_HEADER:
        raise MembershipError(f"unexpected header: {header}")

    out: list[Constituent] = []
    for row in reader:
        symbol = (row.get("Symbol") or "").strip().upper()
        if not symbol:
            continue
        out.append(
            Constituent(
                symbol=symbol,
                company_name=(row.get("Company Name") or "").strip(),
                industry=(row.get("Industry") or "").strip(),
                isin=(row.get("ISIN Code") or "").strip(),
            )
        )

    if not MIN_ROWS <= len(out) <= MAX_ROWS:
        raise MembershipError(f"{len(out)} constituents, expected ~500")
    return out


def fetch(client: httpx.Client, timestamp: str, *, use_cache: bool = True) -> list[Constituent]:
    return parse(fetch_raw(client, timestamp, use_cache=use_cache))
