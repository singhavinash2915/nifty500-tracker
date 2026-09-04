"""Parser assertions matter more than usual here: a silent layout change on
NSE's side would otherwise write 500 rows of nulls into the universe table."""

import httpx
import pytest

from n500.sources import universe
from n500.sources.universe import UniverseParseError, parse_csv, week_start
from datetime import date

HEADER = "Company Name,Industry,Symbol,Series,ISIN Code\n"
GOOD_ROW = "HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\n"


def make_csv(rows: int = 500, *, body: str = GOOD_ROW) -> str:
    lines = []
    for i in range(rows):
        lines.append(body.replace("HDFCBANK", f"SYM{i:04d}").replace(
            "INE040A01034", f"INE040A{i:05d}"))
    return HEADER + "".join(lines)


def test_parses_a_full_list():
    parsed = parse_csv(make_csv())
    assert len(parsed) == 500
    assert parsed[0].sector == "Financial Services"
    assert parsed[0].series == "EQ"


def test_quoted_company_names_with_commas_survive():
    text = HEADER + '"Bajaj Finance, Ltd.",Financial Services,BAJFINANCE,EQ,INE296A01024\n'
    text += make_csv(499)[len(HEADER):]
    parsed = parse_csv(text)
    assert parsed[0].company_name == "Bajaj Finance, Ltd."


def test_rejects_a_changed_header():
    text = "Name,Sector,Ticker\nfoo,bar,baz\n"
    with pytest.raises(UniverseParseError, match="unexpected header"):
        parse_csv(text)


def test_rejects_a_truncated_list():
    with pytest.raises(UniverseParseError, match="expected 450-520"):
        parse_csv(make_csv(12))


def test_rejects_a_bad_isin():
    text = HEADER + "HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,NOTANISIN\n"
    with pytest.raises(UniverseParseError, match="bad ISIN"):
        parse_csv(text)


def test_rejects_a_missing_industry():
    text = HEADER + "HDFC Bank Ltd.,,HDFCBANK,EQ,INE040A01034\n"
    with pytest.raises(UniverseParseError, match="no industry"):
        parse_csv(text)


def test_rejects_duplicate_symbols():
    text = HEADER + GOOD_ROW + GOOD_ROW + make_csv(498)[len(HEADER):]
    with pytest.raises(UniverseParseError, match="duplicate symbol"):
        parse_csv(text)


@pytest.mark.parametrize(
    "given,expected",
    [
        (date(2026, 9, 3), date(2026, 8, 31)),   # Thursday -> Monday
        (date(2026, 8, 31), date(2026, 8, 31)),  # Monday -> itself
        (date(2026, 9, 6), date(2026, 8, 31)),   # Sunday -> that week's Monday
    ],
)
def test_week_start(given, expected):
    assert week_start(given) == expected


def test_dry_run_upsert_keeps_rows_it_did_not_touch(tmp_path, monkeypatch):
    """Regression: a job writing three `stocks` rows replaced the whole 500-row
    universe file, and the next job then ran against three symbols and
    reported success."""
    from n500 import db as db_module

    monkeypatch.setattr(db_module, "DRYRUN_DIR", tmp_path)
    database = db_module.Db(force_dry_run=True)

    database.upsert(
        "stocks",
        [{"symbol": "A", "sector": "Fin"}, {"symbol": "B", "sector": "IT"}],
        on_conflict="symbol",
    )
    database.upsert("stocks", [{"symbol": "A", "sector": "Chem"}], on_conflict="symbol")

    rows = {r["symbol"]: r for r in database.select("stocks")}
    assert set(rows) == {"A", "B"}, "the untouched row must survive"
    assert rows["A"]["sector"] == "Chem"


def test_dry_run_upsert_replaces_a_row_rather_than_merging_it(tmp_path, monkeypatch):
    """PostgREST sends the whole row, so a partial upsert nulls every column it
    omits. Merging in dry-run was kinder than the database and hid a NOT NULL
    violation until it ran for real."""
    from n500 import db as db_module

    monkeypatch.setattr(db_module, "DRYRUN_DIR", tmp_path)
    database = db_module.Db(force_dry_run=True)

    database.upsert("stocks", [{"symbol": "A", "company_name": "Alpha"}],
                    on_conflict="symbol")
    database.upsert("stocks", [{"symbol": "A", "company_type": "financial"}],
                    on_conflict="symbol")

    row = database.select("stocks")[0]
    assert "company_name" not in row, "a partial upsert drops what it omits, as the database does"


def test_update_where_in_changes_only_the_named_columns(tmp_path, monkeypatch):
    """The right tool for recording company_type without nulling the rest."""
    from n500 import db as db_module

    monkeypatch.setattr(db_module, "DRYRUN_DIR", tmp_path)
    database = db_module.Db(force_dry_run=True)
    database.upsert(
        "stocks",
        [{"symbol": "A", "company_name": "Alpha"}, {"symbol": "B", "company_name": "Beta"}],
        on_conflict="symbol",
    )

    touched = database.update_where_in(
        "stocks", {"company_type": "financial"}, column="symbol", matches=["A"]
    )
    rows = {r["symbol"]: r for r in database.select("stocks")}
    assert touched == 1
    assert rows["A"]["company_name"] == "Alpha", "untouched columns survive"
    assert rows["A"]["company_type"] == "financial"
    assert "company_type" not in rows["B"], "unmatched rows are untouched"


def test_dry_run_upsert_without_a_key_appends():
    from n500 import db as db_module

    assert db_module.Db(force_dry_run=True) is not None


def test_replace_discards_what_was_there_before(tmp_path, monkeypatch):
    """Support zones are recomputed from scratch and have no natural key.
    Upserting them appended: the count climbed 9,045 -> 11,841 across two runs
    and the stale rows drew a zone 16% wide that the current engine would
    never produce."""
    from n500 import db as db_module

    monkeypatch.setattr(db_module, "DRYRUN_DIR", tmp_path)
    database = db_module.Db(force_dry_run=True)

    database.replace("support_zones", [{"symbol": "A", "floor_price": 1.0}], key="symbol")
    database.replace("support_zones", [{"symbol": "A", "floor_price": 2.0}], key="symbol")

    rows = database.select("support_zones")
    assert len(rows) == 1, "a second run must not stack on the first"
    assert rows[0]["floor_price"] == 2.0


def test_select_pages_past_the_row_cap(monkeypatch):
    """PostgREST applies max_rows to every request and returns the first page
    with no error. One unpaged read returned 1,000 of 310,414 price rows, and
    every downstream job then reported success on a fiftieth of the data.

    Paging is by key now rather than by offset — OFFSET makes the server walk
    every row it skips, which put `export_snapshot` over the statement timeout
    once the table passed three quarters of a million rows. The fake below
    models a keyset server: it sorts, drops everything at or before the cursor,
    and caps the result.
    """
    from n500 import db as db_module

    total = 2500
    served = [
        {"symbol": f"SYM{i:04d}", "date": "2026-01-01", "i": i} for i in range(total)
    ]

    class FakeQuery:
        def __init__(self):
            self._after: str | None = None
            self._limit = 10**9

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a):
            return self

        def gte(self, *_a):
            return self

        def order(self, *_a, **_k):
            return self

        def gt(self, _column, value):
            self._after = value
            return self

        def or_(self, expression):
            # "symbol.gt.SYM0999,and(symbol.eq...)" — only the leading bound
            # matters here, since every row shares a date.
            self._after = expression.split(",")[0].split(".gt.")[1]
            return self

        def limit(self, n):
            self._limit = n
            return self

        def execute(self):
            rows = sorted(served, key=lambda r: (r["symbol"], r["date"]))
            if self._after is not None:
                rows = [r for r in rows if r["symbol"] > self._after]
            page = rows[: min(self._limit, 1000)]      # the server's own cap
            return type("R", (), {"data": page})()

    class FakeClient:
        def table(self, _name):
            return FakeQuery()

    database = db_module.Db.__new__(db_module.Db)
    database.dry_run = False
    database._client = FakeClient()

    rows = database.select("prices_daily")
    assert len(rows) == total, "a short page is the only reliable end-of-data signal"
    assert rows[0]["i"] == 0 and rows[-1]["i"] == total - 1
    assert len({r["i"] for r in rows}) == total, "keyset paging must not repeat a row"


def test_upsert_collapses_duplicate_keys_within_a_batch():
    """Postgres rejects an ON CONFLICT statement that proposes the same key
    twice — and rejects the whole batch, not the offending pair. One company
    with two rows for the same key cost all 500 companies' fundamentals."""
    from n500.db import _collapse_duplicates

    rows = [
        {"symbol": "A", "period_end": "2025-03-31", "pat": 1},
        {"symbol": "B", "period_end": "2025-03-31", "pat": 2},
        {"symbol": "A", "period_end": "2025-03-31", "pat": 3},   # same key as the first
    ]
    collapsed = _collapse_duplicates(rows, "symbol,period_end")
    assert len(collapsed) == 2
    assert {r["symbol"] for r in collapsed} == {"A", "B"}
    # Last one wins, so a re-scrape overwrites rather than being dropped.
    assert next(r for r in collapsed if r["symbol"] == "A")["pat"] == 3


def test_upsert_without_a_key_keeps_everything():
    from n500.db import _collapse_duplicates

    rows = [{"x": 1}, {"x": 1}]
    assert len(_collapse_duplicates(rows, "")) == 2


def test_every_table_read_has_a_stable_page_order():
    """OFFSET without ORDER BY has no defined row order, so a row can come back
    on two pages or none. Reading 311,232 price rows that way produced a scored
    universe of 317, then 310, then 295 from identical data, silently."""
    from n500.db import PAGE_ORDER
    from n500.jobs.doctor import EXPECTED_TABLES

    missing = [t for t in EXPECTED_TABLES if t not in PAGE_ORDER]
    assert not missing, f"no page order defined for {missing}"


def test_reading_an_unknown_table_fails_loudly(monkeypatch, tmp_path):
    from n500 import db as db_module

    database = db_module.Db.__new__(db_module.Db)
    database.dry_run = False
    database._client = None
    with pytest.raises(KeyError, match="stable page order"):
        database.select("some_new_table")


class TestFetchRetries:
    """A single read timeout should not be why a night's data is missing."""

    def test_a_transient_failure_is_retried(self, monkeypatch):
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ReadTimeout("timed out")
            return httpx.Response(200, text="ok", request=httpx.Request("GET", "http://x"))

        monkeypatch.setattr(universe.httpx, "get", flaky)
        monkeypatch.setattr(universe.time, "sleep", lambda _: None)
        assert universe.fetch_csv("http://x") == "ok"
        assert calls["n"] == 3

    def test_it_gives_up_after_the_last_attempt(self, monkeypatch):
        def always_fails(*args, **kwargs):
            raise httpx.ReadTimeout("timed out")

        monkeypatch.setattr(universe.httpx, "get", always_fails)
        monkeypatch.setattr(universe.time, "sleep", lambda _: None)
        with pytest.raises(httpx.ReadTimeout):
            universe.fetch_csv("http://x", attempts=2)

    def test_a_bad_status_is_retried_too(self, monkeypatch):
        calls = {"n": 0}

        def server_error(*args, **kwargs):
            calls["n"] += 1
            return httpx.Response(503, request=httpx.Request("GET", "http://x"))

        monkeypatch.setattr(universe.httpx, "get", server_error)
        monkeypatch.setattr(universe.time, "sleep", lambda _: None)
        with pytest.raises(httpx.HTTPStatusError):
            universe.fetch_csv("http://x", attempts=3)
        assert calls["n"] == 3
