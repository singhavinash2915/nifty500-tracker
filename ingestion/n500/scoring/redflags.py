"""Hard gates that remove a stock from consideration entirely.

These are not deductions. A business whose reported profit is not turning into
cash does not deserve to lose five points and stay on the list — it deserves to
be off the list, with the reason shown so a name you liked does not simply
vanish.

Evaluability
------------
A check that could not run is recorded as `unknown`, never as passed. Screener
publishes no pledge figure, so the promoter-pledge flag is permanently unknown
from this source; reporting it as clear would be worse than not checking at all,
because it would give false comfort about the single most common way an Indian
mid-cap goes wrong.

Not-applicable is a third state and a real one. ITC, HDFC Bank and Infosys have
no promoter, so promoter-selling cannot apply to them. Treating that as a
failed check would exclude some of the best businesses in the index.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Flag:
    name: str
    verdict: Verdict
    detail: str | None = None

    @property
    def excludes(self) -> bool:
        return self.verdict is Verdict.FAIL


PLEDGE_LIMIT = 20.0
PROMOTER_DROP_LIMIT = 3.0          # percentage points over four quarters
CFO_TO_PAT_FLOOR = 0.5             # three-year cash conversion
DEBTOR_DAYS_JUMP = 1.4             # year on year


def promoter_pledge(pledge_pct: float | None, *, checked: bool) -> Flag:
    if not checked or pledge_pct is None:
        return Flag(
            "promoter_pledge",
            Verdict.UNKNOWN,
            "no pledge figure in this data source — not checked",
        )
    if pledge_pct > PLEDGE_LIMIT:
        return Flag("promoter_pledge", Verdict.FAIL, f"{pledge_pct:.1f}% pledged")
    return Flag("promoter_pledge", Verdict.PASS, f"{pledge_pct:.1f}% pledged")


def promoter_selling(history: list[float | None], *, has_promoter: bool) -> Flag:
    """Promoter holding falling more than 3pp over the last four quarters."""
    if not has_promoter:
        return Flag(
            "promoter_selling", Verdict.NOT_APPLICABLE, "no promoter — professionally managed"
        )
    values = [v for v in history[-5:] if v is not None]
    if len(values) < 2:
        return Flag("promoter_selling", Verdict.UNKNOWN, "not enough quarters")

    drop = values[0] - values[-1]
    if drop > PROMOTER_DROP_LIMIT:
        return Flag("promoter_selling", Verdict.FAIL, f"down {drop:.1f}pp over 4 quarters")
    return Flag("promoter_selling", Verdict.PASS, f"changed {-drop:+.1f}pp")


def cash_conversion(cfo: list[float | None], pat: list[float | None], *, is_financial: bool) -> Flag:
    """Three-year cash from operations against three-year reported profit.

    The single most useful accounting check there is: profit is an opinion,
    cash is a fact. Skipped for lenders, whose operating cash flow swings with
    deposit and loan-book movements and routinely goes deeply negative in a
    perfectly healthy year.
    """
    if is_financial:
        return Flag(
            "cash_conversion",
            Verdict.NOT_APPLICABLE,
            "operating cash flow tracks the loan book for a lender",
        )

    cfo_values = [v for v in cfo[-3:] if v is not None]
    pat_values = [v for v in pat[-3:] if v is not None]
    if len(cfo_values) < 2 or len(pat_values) < 2:
        return Flag("cash_conversion", Verdict.UNKNOWN, "not enough years")

    total_pat = sum(pat_values)
    if total_pat <= 0:
        return Flag("cash_conversion", Verdict.UNKNOWN, "no cumulative profit to compare")

    ratio = sum(cfo_values) / total_pat
    if ratio < CFO_TO_PAT_FLOOR:
        return Flag("cash_conversion", Verdict.FAIL, f"3y CFO is {ratio:.0%} of PAT")
    return Flag("cash_conversion", Verdict.PASS, f"3y CFO is {ratio:.0%} of PAT")


def receivable_bloat(debtor_days: list[float | None], *, is_financial: bool = False) -> Flag:
    """Debtor days jumping year on year — revenue being booked, not collected.

    Skipped for lenders for the same reason as cash conversion: receivables are
    the loan book, so "debtor days" describes the product rather than a
    collection problem.
    """
    if is_financial:
        return Flag(
            "receivable_bloat", Verdict.NOT_APPLICABLE,
            "receivables are the loan book for a lender",
        )
    values = [v for v in debtor_days[-2:] if v is not None]
    if len(values) < 2 or values[0] <= 0:
        return Flag("receivable_bloat", Verdict.UNKNOWN, "not enough history")
    ratio = values[1] / values[0]
    if ratio > DEBTOR_DAYS_JUMP:
        return Flag(
            "receivable_bloat", Verdict.FAIL,
            f"debtor days {values[0]:.0f} -> {values[1]:.0f}",
        )
    return Flag("receivable_bloat", Verdict.PASS, f"debtor days {values[1]:.0f}")


def loss_making(pat: list[float | None]) -> Flag:
    """Losses in two of the last three years."""
    values = [v for v in pat[-3:] if v is not None]
    if len(values) < 2:
        return Flag("loss_making", Verdict.UNKNOWN, "not enough years")
    losses = sum(1 for v in values if v < 0)
    if losses >= 2:
        return Flag("loss_making", Verdict.FAIL, f"{losses} loss-making years of {len(values)}")
    return Flag("loss_making", Verdict.PASS, None)


def evaluate(context: dict) -> list[Flag]:
    return [
        promoter_pledge(context.get("pledge_pct"), checked=context.get("pledge_checked", False)),
        promoter_selling(
            context.get("promoter_history", []), has_promoter=context.get("has_promoter", False)
        ),
        cash_conversion(
            context.get("cfo", []), context.get("pat", []),
            is_financial=context.get("is_financial", False),
        ),
        receivable_bloat(
            context.get("debtor_days", []), is_financial=context.get("is_financial", False)
        ),
        loss_making(context.get("pat", [])),
    ]


def excluded(flags: list[Flag]) -> bool:
    return any(f.excludes for f in flags)


def summarise(flags: list[Flag]) -> list[dict]:
    """Only the interesting ones: failures, and checks that could not run."""
    return [
        {"name": f.name, "verdict": f.verdict.value, "detail": f.detail}
        for f in flags
        if f.verdict in (Verdict.FAIL, Verdict.UNKNOWN)
    ]
