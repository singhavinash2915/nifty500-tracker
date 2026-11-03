"""Three stocks a month: which ones, and that the answer stays put."""

from __future__ import annotations

import json

import pytest

from n500 import db as dbmod
from n500.jobs import compute_monthly_picks as job
from n500.scoring import picks


def score(symbol, conviction, close=100.0, flags=None):
    return {"symbol": symbol, "conviction": conviction, "close": close, "flags": flags or []}


def choose(scores, *, sectors=None, held=(), instruments=None, plans=None):
    symbols = [s["symbol"] for s in scores]
    return [c["symbol"] for c in picks.choose(
        scores,
        instruments=instruments or {},
        sectors=sectors or {s: f"sector {s}" for s in symbols},
        plans=plans if plans is not None else {s: {"plan_stop": 90.0} for s in symbols},
        held=set(held),
    )]


class TestChoose:
    def test_the_highest_conviction_three(self):
        rows = [score("A", 60), score("B", 80), score("C", 70), score("D", 75)]
        assert choose(rows) == ["B", "D", "C"]

    def test_one_per_sector_moves_on_to_the_next_name(self):
        rows = [score("BANK1", 80), score("BANK2", 79), score("PHARMA", 70), score("IT", 60)]
        sectors = {"BANK1": "Fin", "BANK2": "Fin", "PHARMA": "Health", "IT": "Tech"}
        assert choose(rows, sectors=sectors) == ["BANK1", "PHARMA", "IT"]

    def test_a_stock_already_held_is_skipped(self):
        rows = [score("OWNED", 90), score("A", 80), score("B", 70), score("C", 60)]
        assert choose(rows, held={"OWNED"}) == ["A", "B", "C"]

    def test_a_failed_gate_is_skipped(self):
        rows = [score("BAD", 90, flags=[{"verdict": "fail"}]), score("A", 80)]
        assert choose(rows) == ["A"]

    def test_an_unevaluable_gate_is_not_a_failure(self):
        assert choose([score("A", 80, flags=[{"verdict": "unknown"}])]) == ["A"]

    def test_only_ordinary_shares(self):
        rows = [score("GOLDBEES", 90), score("EMBASSY", 85), score("A", 80)]
        kinds = {"GOLDBEES": "etf", "EMBASSY": "reit", "A": "equity"}
        assert choose(rows, instruments=kinds) == ["A"]

    def test_no_usable_stop_cannot_be_sized_so_is_skipped(self):
        rows = [score("NOPLAN", 90), score("ABOVE", 85), score("A", 80)]
        plans = {"ABOVE": {"plan_stop": 105.0}, "A": {"plan_stop": 90.0}}
        assert choose(rows, plans=plans) == ["A"]

    def test_no_conviction_is_never_chosen(self):
        assert choose([score("NONE", None), score("A", 50)]) == ["A"]

    def test_ties_are_repeatable(self):
        rows = [score("Z", 70), score("A", 70)]
        assert choose(rows) == choose(list(reversed(rows))) == ["A", "Z"]

    def test_fewer_than_three_is_fewer_than_three(self):
        assert choose([score("A", 70)]) == ["A"]


class TestBudgets:
    def test_spread_over_every_position_the_plan_can_hold(self):
        position, risk = picks.budgets(2_000_000)
        assert picks.MAX_OPEN == 18
        assert position == pytest.approx(111_111.11)
        assert risk == pytest.approx(6_666.67)

    def test_a_full_book_stays_inside_both_limits(self):
        position, risk = picks.budgets(2_000_000)
        assert position * picks.MAX_OPEN <= 2_000_000 + 1
        assert risk * picks.MAX_OPEN <= 2_000_000 * picks.PORTFOLIO_RISK_BUDGET + 1

    def test_no_capital_means_no_size(self):
        assert picks.budgets(None) == (None, None)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DRYRUN_DIR", tmp_path)
    def write(table, rows):
        (tmp_path / f"{table}.json").write_text(json.dumps(rows))
    write("stocks", [{"symbol": s, "sector": f"sec {s}", "instrument_type": "equity"}
                     for s in ("A", "B", "C", "D", "E")])
    write("portfolio", [{"id": 1, "total_capital": 2_000_000}])
    write("positions", [])
    write("ts_setups", [{"symbol": s, "date": d, "plan_stop": 90.0, "plan_stop_basis": "zone",
                         "plan_target": 120.0}
                        for s in ("A", "B", "C", "D", "E") for d in ("2026-11-02", "2026-11-03")])
    write("scores_daily", [
        *[score(s, c) | {"date": "2026-11-02"} for s, c in (("A", 80), ("B", 70), ("C", 60), ("D", 50))],
        *[score(s, c) | {"date": "2026-11-03"} for s, c in (("D", 99), ("E", 98), ("A", 20))],
        score("A", 99) | {"date": "2026-10-30"},
    ])
    return tmp_path, write


def locked(path):
    return json.loads((path / "monthly_picks.json").read_text())


class TestLocking:
    def test_uses_the_first_session_of_the_month_not_the_latest(self, store):
        path, _ = store
        assert job.main(["--dry-run"]) == 0
        rows = locked(path)
        assert [r["symbol"] for r in rows] == ["A", "B", "C"]
        assert {r["as_of"] for r in rows} == {"2026-11-02"}
        assert {r["month"] for r in rows} == {"2026-11-01"}

    def test_a_later_run_cannot_change_a_locked_month(self, store):
        path, write = store
        job.main(["--dry-run"])
        first = locked(path)
        write("scores_daily", [score("E", 99) | {"date": "2026-11-02"},
                               score("E", 99) | {"date": "2026-11-03"}])
        job.main(["--dry-run"])
        assert locked(path) == first

    def test_a_new_month_gets_its_own_picks(self, store):
        path, write = store
        job.main(["--dry-run"])
        write("ts_setups", [{"symbol": "E", "date": "2026-12-01", "plan_stop": 90.0}])
        write("scores_daily", [score("E", 99) | {"date": "2026-12-01"}])
        job.main(["--dry-run"])
        assert {r["month"] for r in locked(path)} == {"2026-11-01", "2026-12-01"}
