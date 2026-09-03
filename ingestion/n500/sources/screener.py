"""Company fundamentals scraped from Screener.in.

Terms of use
------------
Screener.in's terms prohibit automated access. This was raised and the route
chosen anyway, so the module is built to be a considerate guest rather than to
pretend the issue away: one request every few seconds with jitter, aggressive
disk caching (financials change four times a year, so a page is refetched only
when a result is actually due), and a single interface so that swapping to a
paid feed later is a one-file change.

Two structural facts drive the parser
-------------------------------------
1. Banks and NBFCs use a different row set. A lender reports `Financing Profit`
   and `Financing Margin %` where a manufacturer reports `Operating Profit` and
   `OPM %`, and Screener publishes no ROCE, debtor days or inventory days for
   them. Parsing one shape and calling the other "missing data" would quietly
   void the largest sector in the index — Financial Services is 101 of the 500.

2. A missing Promoters row is information, not absence. ITC, HDFC Bank, Larsen
   and the other professionally managed companies genuinely have no promoter,
   so promoter-selling and pledge checks are *not applicable* rather than
   unknown. Treating that as missing data would exclude some of the best
   businesses in the index.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from ..config import DATA_DIR, settings

BASE_URL = "https://www.screener.in/company/{symbol}/{variant}"
CACHE_DIR = DATA_DIR / "cache" / "screener"

# Financials change quarterly. Refetching daily is scrape volume for no
# information, and the main way to get blocked.
CACHE_TTL_DAYS = 25

MIN_PAUSE = 2.5
MAX_PAUSE = 4.0

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# SEBI LODR gives listed companies 45 days after a quarter to file results, and
# 60 days after the financial year. Screener publishes the period but not the
# filing date, so this is the conservative stand-in: assuming results were
# available LATER than they really were can only make a backtest pessimistic,
# whereas assuming earlier is look-ahead bias. Rows are flagged as estimated.
QUARTER_FILING_LAG_DAYS = 45
ANNUAL_FILING_LAG_DAYS = 60


class ScreenerError(RuntimeError):
    pass


class CompanyNotFound(ScreenerError):
    """404 — usually a symbol that was renamed, merged or demerged away."""


@dataclass
class Fundamentals:
    symbol: str
    company_type: str                      # 'financial' | 'general'
    top_ratios: dict = field(default_factory=dict)
    annual: list[dict] = field(default_factory=list)
    quarterly: list[dict] = field(default_factory=list)
    balance_sheet: list[dict] = field(default_factory=list)
    cash_flow: list[dict] = field(default_factory=list)
    ratios: list[dict] = field(default_factory=list)
    shareholding: list[dict] = field(default_factory=list)
    has_promoter: bool = False

    @property
    def is_financial(self) -> bool:
        return self.company_type == "financial"


# --- fetching --------------------------------------------------------------


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": settings.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=settings.request_timeout,
        follow_redirects=True,
    )


def _cache_path(symbol: str):
    return CACHE_DIR / f"{symbol}.html"


def cache_is_fresh(symbol: str, *, ttl_days: int = CACHE_TTL_DAYS) -> bool:
    path = _cache_path(symbol)
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(days=ttl_days)


def fetch_html(
    client: httpx.Client,
    symbol: str,
    *,
    consolidated: bool = True,
    use_cache: bool = True,
    pause: bool = True,
) -> str:
    if use_cache and cache_is_fresh(symbol):
        return _cache_path(symbol).read_text()

    variant = "consolidated/" if consolidated else ""
    response = client.get(BASE_URL.format(symbol=symbol, variant=variant))

    if response.status_code == 404:
        # Some companies only file standalone accounts; retry once before
        # concluding the symbol is gone.
        if consolidated:
            if pause:
                time.sleep(random.uniform(MIN_PAUSE, MAX_PAUSE))
            return fetch_html(
                client, symbol, consolidated=False, use_cache=False, pause=pause
            )
        raise CompanyNotFound(f"{symbol}: no page on Screener")

    response.raise_for_status()
    html = response.text
    if "Quarterly Results" not in html:
        raise ScreenerError(f"{symbol}: page has no financials section")

    _cache_path(symbol).parent.mkdir(parents=True, exist_ok=True)
    _cache_path(symbol).write_text(html)

    if pause:
        time.sleep(random.uniform(MIN_PAUSE, MAX_PAUSE))
    return html


# --- parsing ---------------------------------------------------------------


NUMBER_RE = re.compile(r"^-?[\d,]+\.?\d*$")


def to_number(raw: str | None) -> float | None:
    """Screener writes '1,234', '-12%', '' and various dashes."""
    if raw is None:
        return None
    text = raw.replace(" ", " ").strip().rstrip("%").replace(",", "").strip()
    if not text or text in {"-", "--", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


PERIOD_RE = re.compile(r"^([A-Z][a-z]{2})\s*(\d{4})\s*(?:(\d{1,2})m)?$")


def parse_period(label: str) -> tuple[date, int] | None:
    """'Mar 2024' -> (2024-03-31, 12). 'TTM' and blanks -> None.

    Screener appends a duration when a company changes its year-end, giving
    labels like 'Mar 202315m' for a fifteen-month year. Those must not crash
    the parse, and the length has to travel with the row: a 15-month year
    inflates revenue by a quarter, so treating it as an ordinary year would
    show a growth spurt that never happened.
    """
    match = PERIOD_RE.match(label.strip())
    if not match:
        return None
    month_name, year_text, months_text = match.groups()
    if month_name not in MONTHS:
        return None

    month, year = MONTHS[month_name], int(year_text)
    months = int(months_text) if months_text else 12
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return end, months


def estimated_filing_date(period_end: date, *, annual: bool) -> date:
    lag = ANNUAL_FILING_LAG_DAYS if annual else QUARTER_FILING_LAG_DAYS
    return period_end + timedelta(days=lag)


def _section_table(soup: BeautifulSoup, section_id: str):
    section = soup.select_one(f"#{section_id}")
    return section.select_one("table") if section else None


def _parse_table(table) -> tuple[list[str], dict[str, list[str]]]:
    """Return (column labels, {row label: cells})."""
    if table is None:
        return [], {}
    headers = [th.get_text(strip=True) for th in table.select("thead th")][1:]
    rows: dict[str, list[str]] = {}
    for tr in table.select("tbody tr"):
        cells = tr.select("td")
        if not cells:
            continue
        label = cells[0].get_text(strip=True).rstrip("+").strip()
        rows[label] = [td.get_text(strip=True) for td in cells[1:]]
    return headers, rows


def _columns_to_records(
    headers: list[str], rows: dict[str, list[str]], mapping: dict[str, str], *, annual: bool
) -> list[dict]:
    """Transpose Screener's period-as-column layout into one record per period."""
    records: list[dict] = []
    for i, label in enumerate(headers):
        parsed = parse_period(label)
        if parsed is None:      # 'TTM' and similar
            continue
        period_end, months = parsed
        record: dict = {
            "period_end": period_end,
            "period_months": months,
            "filed_on": estimated_filing_date(period_end, annual=annual),
            "filed_on_is_estimated": True,
        }
        for source_label, field_name in mapping.items():
            cells = rows.get(source_label)
            record[field_name] = to_number(cells[i]) if cells and i < len(cells) else None
        records.append(record)
    return records


GENERAL_PL = {
    "Sales": "revenue", "Expenses": "expenses", "Operating Profit": "ebitda",
    "OPM %": "opm", "Other Income": "other_income", "Interest": "interest",
    "Depreciation": "depreciation", "Profit before tax": "pbt", "Tax %": "tax_pct",
    "Net Profit": "pat", "EPS in Rs": "eps", "Dividend Payout %": "dividend_payout",
}

FINANCIAL_PL = {
    "Revenue": "revenue", "Interest": "interest", "Expenses": "expenses",
    "Financing Profit": "ebitda", "Financing Margin %": "opm",
    "Other Income": "other_income", "Depreciation": "depreciation",
    "Profit before tax": "pbt", "Tax %": "tax_pct", "Net Profit": "pat",
    "EPS in Rs": "eps", "Dividend Payout %": "dividend_payout",
}

BALANCE_SHEET = {
    "Equity Capital": "equity_capital", "Reserves": "reserves",
    "Borrowings": "borrowings", "Borrowing": "borrowings",
    "Deposits": "deposits", "Other Liabilities": "other_liabilities",
    "Total Liabilities": "total_liabilities", "Fixed Assets": "fixed_assets",
    "Investments": "investments", "Other Assets": "other_assets",
    "Total Assets": "total_assets",
}

CASH_FLOW = {
    "Cash from Operating Activity": "cfo",
    "Cash from Investing Activity": "cfi",
    "Cash from Financing Activity": "cff",
    "Net Cash Flow": "net_cash_flow",
    "Free Cash Flow": "fcf",
}

RATIOS = {
    "Debtor Days": "debtor_days", "Inventory Days": "inventory_days",
    "Days Payable": "days_payable", "Cash Conversion Cycle": "cash_conversion_cycle",
    "Working Capital Days": "working_capital_days", "ROCE %": "roce", "ROE %": "roe",
}

SHAREHOLDING = {
    "Promoters": "promoter_pct", "FIIs": "fii_pct", "DIIs": "dii_pct",
    "Government": "government_pct", "Public": "public_pct", "Others": "others_pct",
}

TOP_RATIOS = {
    "Market Cap": "market_cap", "Current Price": "current_price",
    "Stock P/E": "pe", "Book Value": "book_value", "Dividend Yield": "dividend_yield",
    "ROCE": "roce", "ROE": "roe", "Face Value": "face_value",
}


def parse(html: str, symbol: str) -> Fundamentals:
    soup = BeautifulSoup(html, "lxml")

    pl_headers, pl_rows = _parse_table(_section_table(soup, "profit-loss"))
    if not pl_rows:
        raise ScreenerError(f"{symbol}: no profit & loss table")

    # A lender reports Financing Profit where a manufacturer reports Operating
    # Profit. Everything downstream branches on this.
    is_financial = "Financing Profit" in pl_rows or "Financing Margin %" in pl_rows
    company_type = "financial" if is_financial else "general"
    pl_map = FINANCIAL_PL if is_financial else GENERAL_PL

    q_headers, q_rows = _parse_table(_section_table(soup, "quarters"))
    bs_headers, bs_rows = _parse_table(_section_table(soup, "balance-sheet"))
    cf_headers, cf_rows = _parse_table(_section_table(soup, "cash-flow"))
    ra_headers, ra_rows = _parse_table(_section_table(soup, "ratios"))
    sh_headers, sh_rows = _parse_table(_section_table(soup, "shareholding"))

    top: dict = {}
    for li in soup.select("#top-ratios li"):
        name = li.select_one(".name")
        value = li.select_one(".value")
        if not name or not value:
            continue
        key = TOP_RATIOS.get(name.get_text(strip=True))
        if key:
            top[key] = to_number(
                value.get_text(" ", strip=True).replace("₹", "").split("/")[0]
            )

    result = Fundamentals(
        symbol=symbol,
        company_type=company_type,
        top_ratios=top,
        annual=_columns_to_records(pl_headers, pl_rows, pl_map, annual=True),
        quarterly=_columns_to_records(q_headers, q_rows, pl_map, annual=False),
        balance_sheet=_columns_to_records(bs_headers, bs_rows, BALANCE_SHEET, annual=True),
        cash_flow=_columns_to_records(cf_headers, cf_rows, CASH_FLOW, annual=True),
        ratios=_columns_to_records(ra_headers, ra_rows, RATIOS, annual=True),
        shareholding=_columns_to_records(sh_headers, sh_rows, SHAREHOLDING, annual=False),
        # Absence is a fact about the company, not a gap in the data.
        has_promoter="Promoters" in sh_rows,
    )

    _assert_sane(result)
    return result


def _assert_sane(data: Fundamentals) -> None:
    """Fail loudly on a layout change instead of writing rows of nulls."""
    if len(data.annual) < 3:
        raise ScreenerError(f"{data.symbol}: only {len(data.annual)} annual periods")
    if len(data.quarterly) < 4:
        raise ScreenerError(f"{data.symbol}: only {len(data.quarterly)} quarters")

    revenues = [r["revenue"] for r in data.annual if r.get("revenue") is not None]
    if not revenues:
        raise ScreenerError(f"{data.symbol}: no revenue in any annual period")
    if all(v <= 0 for v in revenues):
        raise ScreenerError(f"{data.symbol}: every annual revenue is non-positive")

    margins = [r["opm"] for r in data.annual if r.get("opm") is not None]
    if margins and not all(-200 <= m <= 200 for m in margins):
        raise ScreenerError(f"{data.symbol}: operating margin outside +/-200%")

    for record in data.shareholding:
        total = sum(
            v for k, v in record.items()
            if k.endswith("_pct") and isinstance(v, (int, float))
        )
        if total and not 90 <= total <= 110:
            raise ScreenerError(
                f"{data.symbol}: shareholding for {record['period_end']} sums to {total:.1f}%"
            )
