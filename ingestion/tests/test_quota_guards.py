"""Guards against running the free plan out of quota again.

The project that held this tracker was restricted, and then lost, because the
nightly pipeline read far more than it needed and nothing measured it. These
tests pin down the pieces that now prevent a repeat: bounded reads that do not
grow with the table, a byte counter on every read, and an alarm that trips
while there is still quota left.
"""

from __future__ import annotations

import gzip
import json
from datetime import date

import pytest

from n500 import db as dbmod
from n500.jobs import compute_scores, verify

MB = 1024**2


class TestEgressBudget:
    def test_a_normal_month_passes(self):
        checks = verify.egress_checks(200 * MB, date(2026, 10, 15))
        assert all(c.ok for c in checks)

    def test_over_budget_fails_the_run(self):
        # Fatal, so the nightly exits non-zero and the failure notification
        # fires while four fifths of the quota is still unused.
        over = verify.PIPELINE_EGRESS_BUDGET + 1
        budget, course = verify.egress_checks(over, date(2026, 10, 28))
        assert not budget.ok and budget.fatal

    def test_on_course_to_exceed_is_a_warning_not_a_failure(self):
        # 600MB by the 10th projects to ~1.8GB for October: worth seeing,
        # not yet worth failing the run over.
        budget, course = verify.egress_checks(600 * MB, date(2026, 10, 10))
        assert budget.ok
        assert not course.ok and not course.fatal

    @pytest.mark.parametrize("today", [date(2026, 12, 31), date(2027, 2, 28), date(2028, 2, 29)])
    def test_month_length_is_right_at_year_end_and_in_february(self, today):
        # A wrong month length would divide by the wrong number of days.
        budget, course = verify.egress_checks(10 * MB, today)
        assert course.ok

    def test_the_budget_leaves_most_of_the_quota_to_the_web_app(self):
        assert verify.PIPELINE_EGRESS_BUDGET <= verify.EGRESS_QUOTA / 4


@pytest.fixture
def dry(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DRYRUN_DIR", tmp_path)
    return dbmod.Db(force_dry_run=True), tmp_path


def write(tmp_path, table, rows):
    (tmp_path / f"{table}.json").write_text(json.dumps(rows))


class TestLatestReads:
    ROWS = [
        {"symbol": "AAA", "date": "2026-09-01", "v": 1},
        {"symbol": "BBB", "date": "2026-09-01", "v": 2},
        {"symbol": "AAA", "date": "2026-09-02", "v": 3},
        {"symbol": "BBB", "date": "2026-09-02", "v": 4},
        {"symbol": "AAA", "date": "2026-08-20", "v": 0},
    ]

    def test_recent_values_are_distinct_and_newest_first(self, dry):
        db, path = dry
        write(path, "scores_daily", self.ROWS)
        assert db.recent_values("scores_daily", "date", 2) == ["2026-09-02", "2026-09-01"]

    def test_asking_for_more_values_than_exist_returns_what_there_is(self, dry):
        db, path = dry
        write(path, "scores_daily", self.ROWS)
        assert len(db.recent_values("scores_daily", "date", 10)) == 3

    def test_select_latest_returns_only_the_newest_date(self, dry):
        db, path = dry
        write(path, "fundamental_scores", self.ROWS)
        rows = db.select_latest("fundamental_scores")
        assert {r["date"] for r in rows} == {"2026-09-02"}
        assert {r["symbol"] for r in rows} == {"AAA", "BBB"}

    def test_an_empty_table_is_empty_not_an_error(self, dry):
        db, _ = dry
        assert db.recent_values("scores_daily", "date") == []
        assert db.select_latest("ts_setups") == []


class FakeQuery:
    """Just enough of the PostgREST builder to page a table."""

    def __init__(self, rows):
        self.rows = rows
        self._limit = None
        self._desc = False
        self._lt = None

    def select(self, *_): return self
    def eq(self, *_): return self
    def gte(self, *_): return self
    def order(self, column, desc=False):
        self._desc = desc
        return self
    def lt(self, column, value):
        self._lt = (column, value)
        return self
    def gt(self, *_): return self
    def or_(self, *_): return self
    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        rows = self.rows
        if self._lt:
            c, v = self._lt
            rows = [r for r in rows if r[c] < v]
        rows = sorted(rows, key=lambda r: r["date"], reverse=self._desc)
        out = type("R", (), {})()
        out.data = rows[: self._limit]
        return out


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.requests = 0

    def table(self, _):
        self.requests += 1
        return FakeQuery(self.rows)


def live_db(rows):
    db = dbmod.Db(force_dry_run=True)
    db.dry_run = False
    db._client = FakeClient(rows)
    return db


class TestByteCounter:
    def test_every_page_read_is_counted(self):
        rows = [{"symbol": f"S{i:03d}", "date": "2026-09-01"} for i in range(10)]
        db = live_db(rows)
        db.select("stocks")
        assert db.bytes_read == len(json.dumps(rows))

    def test_recent_values_costs_one_row_per_value_however_big_the_table(self):
        # The point of the helper: finding two dates must not scale with the
        # number of rows, which is what the whole-table read did.
        rows = [{"date": f"2026-{m:02d}-{d:02d}"} for m in range(1, 10) for d in range(1, 28)]
        db = live_db(rows)
        assert db.recent_values("scores_daily", "date", 2) == ["2026-09-27", "2026-09-26"]
        assert db._client.requests == 2
        assert db.bytes_read < 100


class TestScoreArchive:
    def test_tonights_ranking_is_kept_with_the_columns_that_matter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(compute_scores, "ARCHIVE_DIR", tmp_path)
        rows = [{"symbol": "AAA", "date": "2026-09-29", "conviction": 71.2,
                 "on_buylist": True, "flags": ["x"] * 50}]
        path = compute_scores.archive_scores(rows, date(2026, 9, 29))
        with gzip.open(path, "rt") as f:
            kept = json.load(f)
        assert kept[0]["conviction"] == 71.2
        assert "flags" not in kept[0]       # slim: the track record, not the reasoning
        assert path.name == "2026-09-29.json.gz"
