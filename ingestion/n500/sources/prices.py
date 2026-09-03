"""Daily OHLCV from the Yahoo Finance chart endpoint.

Yahoo rate-limits hard and without warning: a burst of plain requests starts
returning `429 Too Many Requests` as HTML within a few seconds, and the block
is per-IP for a cooldown period rather than per-request. Three things keep a
500-symbol sweep alive:

  * a shared client whose cookie jar is primed against finance.yahoo.com,
  * exponential backoff with jitter on 429 and 5xx,
  * a deliberate pause between symbols.

At the default pacing a full sweep takes roughly 10 minutes. That is fine for a
job that runs once a night, and it is the difference between finishing and
getting blocked halfway.

NSE symbols map to Yahoo by suffixing `.NS`, with a few exceptions handled in
SYMBOL_OVERRIDES.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

from ..config import settings

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
PRIMER_URL = "https://finance.yahoo.com/"

# Yahoo's ticker for the Nifty 500 — the benchmark for relative strength.
INDEX_SYMBOL = "^CRSLDX"

SYMBOL_OVERRIDES: dict[str, str] = {
    # NSE symbol -> Yahoo symbol, for the handful that do not follow SYMBOL.NS
    "M&M": "M&M.NS",
    "M&MFIN": "M&MFIN.NS",
    "L&TFH": "LTF.NS",
}

RETRY_STATUSES = {429, 500, 502, 503, 504}


class PriceFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bar:
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adj_close: float | None
    volume: int | None


def yahoo_symbol(nse_symbol: str) -> str:
    # Index tickers ('^CRSLDX') are already Yahoo-native and take no suffix.
    if nse_symbol.startswith("^"):
        return nse_symbol
    return SYMBOL_OVERRIDES.get(nse_symbol, f"{nse_symbol}.NS")


def make_client() -> httpx.Client:
    """A client with Yahoo's consent cookies already in the jar."""
    client = httpx.Client(
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=settings.request_timeout,
        follow_redirects=True,
    )
    try:
        client.get(PRIMER_URL)
    except httpx.HTTPError:
        # Priming is an optimisation, not a requirement.
        pass
    return client


def _get_with_backoff(
    client: httpx.Client,
    url: str,
    params: dict,
    *,
    max_attempts: int = 6,
    base_delay: float = 1.5,
) -> httpx.Response:
    last_status: int | None = None
    for attempt in range(max_attempts):
        response = client.get(url, params=params)
        if response.status_code not in RETRY_STATUSES:
            response.raise_for_status()
            return response
        last_status = response.status_code
        # 1.5, 3, 6, 12, 24 seconds plus jitter.
        time.sleep(base_delay * (2**attempt) + random.uniform(0, 1.0))
    raise PriceFetchError(f"gave up after {max_attempts} attempts (last {last_status})")


def parse_chart(payload: dict, *, symbol: str) -> list[Bar]:
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise PriceFetchError(f"{symbol}: {chart['error']}")

    results = chart.get("result")
    if not results:
        raise PriceFetchError(f"{symbol}: empty chart result")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    adj = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")

    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    if not timestamps:
        raise PriceFetchError(f"{symbol}: no timestamps")

    lengths = {len(timestamps), len(opens), len(highs), len(lows), len(closes)}
    if len(lengths) != 1:
        raise PriceFetchError(f"{symbol}: ragged series lengths {lengths}")

    bars: list[Bar] = []
    for i, ts in enumerate(timestamps):
        close = closes[i]
        if close is None:
            # Yahoo emits null rows for holidays and halts. Dropping them is
            # correct: a null is "no trading", not "price of zero".
            continue
        bars.append(
            Bar(
                date=datetime.fromtimestamp(ts, tz=timezone.utc).date(),
                open=opens[i],
                high=highs[i],
                low=lows[i],
                close=close,
                adj_close=adj[i] if adj and i < len(adj) else close,
                volume=int(volumes[i]) if i < len(volumes) and volumes[i] is not None else None,
            )
        )

    if not bars:
        raise PriceFetchError(f"{symbol}: every bar was null")

    return bars


def fetch_bars(
    client: httpx.Client,
    nse_symbol: str,
    *,
    range_: str = "2y",
    interval: str = "1d",
) -> list[Bar]:
    ysym = yahoo_symbol(nse_symbol)
    response = _get_with_backoff(
        client,
        CHART_URL.format(symbol=ysym),
        {"range": range_, "interval": interval, "events": "div,split"},
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise PriceFetchError(f"{nse_symbol}: non-JSON response") from exc
    return parse_chart(payload, symbol=nse_symbol)


def to_rows(symbol: str, bars: list[Bar]) -> list[dict]:
    return [
        {
            "symbol": symbol,
            "date": bar.date.isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "adj_close": bar.adj_close,
            "volume": bar.volume,
        }
        for bar in bars
    ]
