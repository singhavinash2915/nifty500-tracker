"""What is worth interrupting someone for.

An alert system earns its keep by being quiet. The failure mode is not missing
a signal, it is firing so often that the one that mattered scrolls past with
the rest — so every rule here is a *transition*, never a state. "RSI is below
30" is a condition that stays true for weeks and would fire every night;
"crossed below 30 today" happens once.

That is also why each alert carries a dedupe key. A rule that legitimately
re-fires — a stop breached again after a recovery — should, but the same
underlying event on consecutive nights should not.

Rules are ordered by how much they should interrupt: a stop breached on a real
position outranks any amount of screening news.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"      # money at risk right now
    ACTION = "action"          # a decision is available today
    INFO = "info"              # worth knowing, not worth acting on tonight


SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.ACTION: 1, Severity.INFO: 2}


def _number(value) -> float | None:
    """A missing field, whatever shape it arrives in.

    Rows come back from pandas, so an absent target is NaN rather than None —
    and every comparison against NaN is False, which let `target_reached` sail
    past its own guard and announce that ITC had "reached 266.30 against a nan
    target". Anything that is not a real number is absent.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number      # NaN is the only value != itself


@dataclass(frozen=True)
class Alert:
    symbol: str
    rule: str
    severity: Severity
    message: str
    payload: dict = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        """Identifies the underlying event, not the night it was noticed."""
        extra = self.payload.get("dedupe")
        return f"{self.symbol}:{self.rule}" + (f":{extra}" if extra else "")


# --- position rules (money at risk) ---------------------------------------


def stop_breached(position: dict, close: float) -> Alert | None:
    stop = _number(position.get("stop_price"))
    if stop is None or close > stop:
        return None
    return Alert(
        symbol=position["symbol"],
        rule="stop_breached",
        severity=Severity.CRITICAL,
        message=f"closed at {close:.2f}, below the {stop:.2f} stop",
        payload={"close": close, "stop": stop, "dedupe": position.get("id")},
    )


def target_reached(position: dict, close: float) -> Alert | None:
    target = _number(position.get("target_price"))
    if target is None or close < target:
        return None
    entry = _number(position.get("entry_price"))
    gain = f", up {(close / entry - 1) * 100:.0f}% from entry" if entry else ""
    return Alert(
        symbol=position["symbol"],
        rule="target_reached",
        severity=Severity.ACTION,
        message=f"reached {close:.2f} against a {target:.2f} target{gain}",
        payload={"close": close, "target": target, "dedupe": position.get("id")},
    )


def thesis_broken(position: dict, today: dict) -> Alert | None:
    """A red flag appeared on something already owned.

    The most important alert in the system that is not about price: the reason
    for holding has stopped being true.
    """
    failures = [f for f in (today.get("flags") or []) if f.get("verdict") == "fail"]
    if not failures:
        return None
    names = ", ".join(f["name"].replace("_", " ") for f in failures)
    return Alert(
        symbol=position["symbol"],
        rule="thesis_broken",
        severity=Severity.CRITICAL,
        message=f"now fails a hard gate ({names}) — the reason for holding has changed",
        payload={"flags": failures, "dedupe": ",".join(sorted(f["name"] for f in failures))},
    )


def score_decayed(position: dict, today: dict, *, floor: int = 7) -> Alert | None:
    """The score that justified the entry has fallen out of the top deciles."""
    decile = _number(today.get("decile"))
    if decile is None or int(decile) >= floor:
        return None
    return Alert(
        symbol=position["symbol"],
        rule="score_decayed",
        severity=Severity.ACTION,
        message=f"has slipped to decile {int(decile)} — the entry case has weakened",
        payload={"decile": int(decile), "dedupe": int(decile)},
    )


# --- screening rules (transitions in the ranking) --------------------------


def entered_top_decile(today: dict, yesterday: dict | None) -> Alert | None:
    if yesterday is None:
        return None
    now, before = _number(today.get("decile")), _number(yesterday.get("decile"))
    if now is None or before is None or int(now) < 10 or int(before) >= 10:
        return None
    return Alert(
        symbol=today["symbol"],
        rule="entered_top_decile",
        severity=Severity.INFO,
        message=f"moved into the top decile (blend {today.get('blended')})",
        payload={"blended": today.get("blended")},
    )


def setup_triggered(today: dict, yesterday: dict | None) -> Alert | None:
    """The support setup printed its confirmation.

    The alert this whole engine exists for: the difference between a stock
    sitting at support and one turning off it, which is the moment the reward
    to risk is best and the window is shortest.
    """
    if today.get("setup_status") != "triggered":
        return None
    if yesterday is not None and yesterday.get("setup_status") == "triggered":
        return None

    confirmations = ", ".join(today.get("confirmations") or []) or "confirmed"
    reward = _number(today.get("reward_risk"))
    tail = f", {reward:.1f}:1 to the target" if reward else ""
    return Alert(
        symbol=today["symbol"],
        rule="setup_triggered",
        severity=Severity.ACTION,
        message=f"reversal confirmed at support ({confirmations}){tail}",
        payload={
            "stop": today.get("stop_price"),
            "target": today.get("target_price"),
            "reward_risk": reward,
            "dedupe": today.get("zone_floor"),
        },
    )


def zone_broken(today: dict, yesterday: dict | None) -> Alert | None:
    """A watched support gave way."""
    if yesterday is None:
        return None
    if yesterday.get("setup_status") == "none" or today.get("setup_status") != "none":
        return None
    if today.get("reason") not in ("no live zone below price",):
        return None
    return Alert(
        symbol=today["symbol"],
        rule="zone_broken",
        severity=Severity.INFO,
        message="lost the support zone it was resting on",
        payload={"was": yesterday.get("zone_floor")},
    )


def new_red_flag(today: dict, yesterday: dict | None) -> Alert | None:
    if yesterday is None:
        return None
    now = {f["name"] for f in (today.get("flags") or []) if f.get("verdict") == "fail"}
    before = {f["name"] for f in (yesterday.get("flags") or []) if f.get("verdict") == "fail"}
    fresh = now - before
    if not fresh:
        return None
    return Alert(
        symbol=today["symbol"],
        rule="new_red_flag",
        severity=Severity.INFO,
        message=f"newly fails {', '.join(sorted(n.replace('_', ' ') for n in fresh))}",
        payload={"flags": sorted(fresh), "dedupe": ",".join(sorted(fresh))},
    )


# --- evaluation ------------------------------------------------------------


def for_position(position: dict, today: dict | None, close: float | None) -> list[Alert]:
    if today is None:
        return []
    out = []
    if close is not None:
        out += [a for a in (stop_breached(position, close), target_reached(position, close)) if a]
    out += [a for a in (thesis_broken(position, today), score_decayed(position, today)) if a]
    return out


def for_screen(today: dict, yesterday: dict | None) -> list[Alert]:
    candidates = (
        setup_triggered(today, yesterday),
        entered_top_decile(today, yesterday),
        zone_broken(today, yesterday),
        new_red_flag(today, yesterday),
    )
    return [a for a in candidates if a]


def rank(alerts: list[Alert]) -> list[Alert]:
    """Most interrupting first — a breached stop outranks any screening news."""
    return sorted(alerts, key=lambda a: (SEVERITY_ORDER[a.severity], a.symbol))


def suppress_seen(alerts: list[Alert], already: set[str]) -> list[Alert]:
    """Drop anything whose underlying event has already been reported."""
    return [a for a in alerts if a.dedupe_key not in already]
