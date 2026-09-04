"""Intraday index quotes from NSE's own snapshot endpoint.

`/api/allIndices` answers without cookie priming and returns every index with a
live price and the exchange's own timestamp. Its siblings do not: the per-stock
`/api/quote-equity` returns 403 to this connection and `/api/equity-stockIndices`
returns 404, so there is no free route to live prices for the 500 constituents.
Yahoo's chart endpoint would give delayed ones but rate-limits this IP to 429.

Live prices for individual stocks therefore need a broker API — Angel One's
SmartAPI is free with an account, Zerodha's Kite is paid — and an account to go
with it. What is available without one is the index level, which is the more
useful half anyway: it tells you whether a stock is falling on its own or with
everything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

import httpx

from ..config import settings

ALL_INDICES_URL = "https://www.nseindia.com/api/allIndices"

# Shown on the site. Names are NSE's, in upper case, and differ from the
# archive's title case — "NIFTY 50" here, "Nifty 50" in index_prices.
TRACKED = (
    "NIFTY 50",
    "NIFTY BANK",
    "NIFTY MIDCAP 150",
    "NIFTY SMALLCAP 250",
    "NIFTY IT",
    "NIFTY AUTO",
    "NIFTY PHARMA",
    "NIFTY FMCG",
    "NIFTY METAL",
    "NIFTY REALTY",
    "INDIA VIX",
    "NIFTY 500",
)

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


class LiveQuoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveQuote:
    name: str
    last: float | None
    change: float | None
    pct_change: float | None
    open: float | None
    high: float | None
    low: float | None
    prev_close: float | None
    year_high: float | None
    year_low: float | None
    as_of: str | None


def market_is_open(now: datetime | None = None) -> bool:
    """Weekday, inside the session. Holidays are not modelled.

    A poll on a holiday costs one request and returns the previous close, which
    is harmless — the exchange timestamp on the row says how stale it is, so a
    reader can tell without this function being clever about the calendar.
    """
    now = (now or datetime.now(IST)).astimezone(IST)
    return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=settings.request_timeout,
        follow_redirects=True,
    )


def _number(value) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch(client: httpx.Client, *, keep: tuple[str, ...] = TRACKED) -> list[LiveQuote]:
    response = client.get(ALL_INDICES_URL)
    if response.status_code != 200:
        raise LiveQuoteError(f"allIndices returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise LiveQuoteError("allIndices did not return JSON") from exc

    rows = payload.get("data")
    if not rows:
        raise LiveQuoteError("allIndices returned no data")

    stamp = payload.get("timestamp")
    wanted = {k.upper() for k in keep} if keep else None

    out: list[LiveQuote] = []
    for row in rows:
        name = str(row.get("index") or "").strip()
        if wanted is not None and name.upper() not in wanted:
            continue
        out.append(
            LiveQuote(
                name=name,
                last=_number(row.get("last")),
                change=_number(row.get("variation")),
                pct_change=_number(row.get("percentChange")),
                open=_number(row.get("open")),
                high=_number(row.get("high")),
                low=_number(row.get("low")),
                prev_close=_number(row.get("previousClose")),
                year_high=_number(row.get("yearHigh")),
                year_low=_number(row.get("yearLow")),
                as_of=stamp,
            )
        )

    if not out:
        raise LiveQuoteError("none of the tracked indices were in the response")
    return out


def to_row(quote: LiveQuote) -> dict:
    return {
        "name": quote.name,
        "last": quote.last,
        "change": quote.change,
        "pct_change": quote.pct_change,
        "open": quote.open,
        "high": quote.high,
        "low": quote.low,
        "prev_close": quote.prev_close,
        "year_high": quote.year_high,
        "year_low": quote.year_low,
        "as_of": quote.as_of,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
