"""Who is on the buy list, and — more importantly — who stays on it.

The problem
-----------
"Everyday its giving different list ... its not practical."

Measured: of the top ten by conviction, 10% survived to the next rebalance. Even
after carrying the one-bar events forward for a quarter, which fixed the larger
half of the cause, 68% of a plain top-ten was replaced each month. A name two
places outside the cut vanishes entirely, then returns a week later having done
nothing.

The fix is the one index providers use, and it is not a subtlety: **admit on a
tighter rank than you evict on.** A stock enters at rank 10 or better and leaves
only once it falls past rank 25, so ordinary jostling in the middle changes
nothing. Turnover falls from 68% to 50% a month and the median forward return
moves 0.7pp, which is inside the noise of a training-period measurement.

Why the band is 10 and 25
-------------------------
The turnover curve is smooth and the return cost is flat across every band
tried, so there is no peak to find and nothing to overfit to. 2.5x is the
conventional ratio, and picking a conventional number over the best-looking one
is the right instinct when the measurement cannot tell them apart.

What this is not
----------------
Not a hold rule. Once a position is open, the stop and the thesis govern it —
not tomorrow's ranking, and not this list. A name leaving the buy list is a
statement about new money, not a sell signal. Conflating the two is how a
six-month thesis gets closed in week three because a screener reshuffled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Rank at or better than this to join.
ENTER_AT = 10

# Rank worse than this to leave. The gap between the two is the whole mechanism.
EXIT_PAST = 25


@dataclass(frozen=True)
class Membership:
    symbol: str
    rank: int
    since: date
    is_new: bool


def apply_band(
    ranked: list[str],
    previous: dict[str, date],
    today: date,
    *,
    enter_at: int = ENTER_AT,
    exit_past: int = EXIT_PAST,
) -> list[Membership]:
    """Today's list, given yesterday's and a fresh ranking.

    `ranked` is every scored symbol, best first. `previous` maps a symbol that
    was on the list to the date it joined, so a name that survives keeps its
    original date rather than appearing to be new every night.

    Incumbents are resolved before newcomers. A stock holding rank 20 keeps its
    place and the list is filled to `enter_at` from whoever is left, so the list
    is a stable set of about `enter_at` names rather than a fresh top ten daily.
    """
    rank_of = {symbol: i + 1 for i, symbol in enumerate(ranked)}

    kept = [
        Membership(symbol, rank_of[symbol], joined, is_new=False)
        for symbol, joined in previous.items()
        if rank_of.get(symbol, 10**9) <= exit_past
    ]
    kept.sort(key=lambda m: m.rank)

    held = {m.symbol for m in kept}
    room = max(enter_at - len(kept), 0)
    added = [
        Membership(symbol, rank_of[symbol], today, is_new=True)
        for symbol in ranked[:enter_at]
        if symbol not in held
    ][:room]

    return sorted([*kept, *added], key=lambda m: m.rank)
