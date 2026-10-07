"""Job: the whole pipeline, in order, once a night.

    python -m n500.jobs.run_nightly --dry-run
    python -m n500.jobs.run_nightly --days 10

Ordering is a dependency chain, not a preference, so a failure part-way through
must not let later steps run on stale inputs and report success. Each step
declares what it needs; if a prerequisite failed, the step is *skipped* and
recorded as skipped rather than run and recorded as fine.

The universe refresh runs first and every night. It is the cheapest step and it
is what accumulates `index_membership`, which is the only cure for the
survivorship bias in the backtest — every night this runs is a night of
point-in-time history the next sweep can use.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime

from ..db import Db

JOB = "run_nightly"


@dataclass
class Step:
    name: str
    module: str
    args: list[str] = field(default_factory=list)
    needs: tuple[str, ...] = ()
    # A step that may fail without poisoning what follows — the scrapers, whose
    # upstream is outside our control.
    tolerate_failure: bool = False
    # Recomputes from prices and fundamentals, so it has nothing to do on a
    # night when neither changed. See is_idle.
    needs_new_data: bool = False


# Everything downstream of the loaders except verify. A weekend or holiday
# brings no new session, and these steps then recompute exactly what they
# computed the night before: three of seven runs in the first week of October
# 2026, about 25MB of egress each for nothing. verify is deliberately not here.
# If the price loader ever broke and loaded nothing, every night would look idle
# and the screen would go stale behind a run reporting success; verify's
# freshness check is what catches that, and it costs about a megabyte.
RECOMPUTE = ("technicals", "fundamental_scores", "zones", "backdrop", "scores",
             "positions", "alerts", "prune")


def is_idle(prices_before: int | None, prices_after: int | None,
            outcomes: dict[str, str]) -> bool:
    """Nothing new arrived tonight, so there is nothing to recompute.

    By row count rather than by date: a weekend that backfills a company newly
    added to the index brings no new session but plenty of new rows. A count it
    could not read means not idle, because running everything is the safe way
    to be wrong. A Sunday fundamentals refresh that succeeded is new data too.
    """
    if prices_before is None or prices_after is None:
        return False
    if outcomes.get("fundamentals") == "ok":
        return False
    return prices_after == prices_before


def _price_rows(db: Db) -> int | None:
    try:
        return db.count("prices_daily")
    except Exception:  # noqa: BLE001 - unknown means "run everything"
        return None


def plan(days: int, *, dry_run: bool, skip_fundamentals: bool) -> list[Step]:
    common = ["--dry-run"] if dry_run else []
    steps = [
        Step("universe", "load_universe", common),
        Step("prices", "load_prices", [*common, "--days", str(days)], needs=("universe",)),
        Step("index", "load_index", [*common, "--days", str(days)], needs=("universe",)),
    ]
    if not skip_fundamentals:
        steps.append(
            Step("fundamentals", "load_fundamentals", common,
                 needs=("universe",), tolerate_failure=True)
        )
    steps += [
        # `--tail`: technicals are wholly derived from prices, and nothing
        # reads them deeply — scoring takes 21 days, the snapshot 60, and
        # the backtest recomputes them from prices rather than reading the
        # table at all. Written in full it reached 822,046 rows and 323MB,
        # two thirds of a 500MB database, for 120 days of usable data.
        Step("technicals", "compute_technicals", [*common, "--tail", "120"],
             needs=("prices", "index")),
        Step("fundamental_scores", "compute_fundamental_scores", common,
             needs=("universe",), tolerate_failure=True),
        Step("zones", "compute_zones", common, needs=("prices",)),
        Step("backdrop", "compute_backdrop", common, needs=("prices",)),
        Step("scores", "compute_scores", common, needs=("technicals", "zones")),
        # No export_snapshot here. It wrote fallback files into the running
        # machine's web/public, which the site never deploys (it builds from
        # git), and read 22MB a night from Supabase to do it: two thirds of the
        # pipeline's egress, for nothing. It is still run by hand when the
        # committed fallback needs refreshing.
        Step("positions", "compute_positions", common, needs=("scores",)),
        Step("alerts", "compute_alerts", [*common, "--quiet"], needs=("scores",)),
        # Last, and it needs nothing: the invariants should run even when a
        # step failed, because the interesting question then is exactly what
        # the half-finished output looks like.
        Step("prune", "prune", common, needs=("scores",)),
        Step("verify", "verify", common),
    ]
    for step in steps:
        step.needs_new_data = step.name in RECOMPUTE
    return steps


def run_step(step: Step) -> tuple[str, str | None]:
    module = importlib.import_module(f"n500.jobs.{step.module}")
    code = module.main(step.args)
    return ("ok" if code == 0 else "failed", None if code == 0 else f"exit code {code}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the nightly pipeline")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", type=int, default=10, help="calendar days of prices to top up")
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="fundamentals change quarterly; skip on nights when nothing is due",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="recompute even if no new prices arrived (after a code change, say)",
    )
    args = parser.parse_args(argv)

    steps = plan(args.days, dry_run=args.dry_run, skip_fundamentals=args.skip_fundamentals)
    outcomes: dict[str, str] = {}
    started = time.monotonic()
    db = Db(force_dry_run=args.dry_run)
    prices_before = _price_rows(db)
    idle: bool | None = None          # decided at the first step that needs new data

    print(f"[{JOB}] starting {datetime.now():%Y-%m-%d %H:%M:%S} — {len(steps)} steps")

    for step in steps:
        # "idle" satisfies a dependency: an idle technicals step left last
        # night's output in place, which is what a dependent step would read.
        blockers = [n for n in step.needs
                    if outcomes.get(n) not in ("ok", "tolerated", "idle")]
        if blockers:
            outcomes[step.name] = "skipped"
            print(f"[{JOB}] SKIP {step.name}: needs {', '.join(blockers)}, which did not succeed")
            continue

        if step.needs_new_data and not args.force:
            if idle is None:
                idle = is_idle(prices_before, _price_rows(db), outcomes)
                if idle:
                    print(f"[{JOB}] no new prices since the last run: nothing to recompute")
            if idle:
                outcomes[step.name] = "idle"
                print(f"[{JOB}] IDLE    {step.name}")
                continue

        mark = time.monotonic()
        try:
            status, detail = run_step(step)
        except Exception as exc:  # noqa: BLE001
            status, detail = "failed", f"{type(exc).__name__}: {exc}"
            traceback.print_exc(limit=3)

        elapsed = time.monotonic() - mark
        if status == "failed" and step.tolerate_failure:
            outcomes[step.name] = "tolerated"
            print(f"[{JOB}] WARN {step.name} failed in {elapsed:.0f}s ({detail}) — continuing, "
                  "later steps will use the last good data")
        else:
            outcomes[step.name] = status
            print(f"[{JOB}] {status.upper():7} {step.name} in {elapsed:.0f}s"
                  + (f" — {detail}" if detail else ""))

    total = time.monotonic() - started
    failed = [n for n, s in outcomes.items() if s == "failed"]
    skipped = [n for n, s in outcomes.items() if s == "skipped"]

    print(f"\n[{JOB}] finished in {total / 60:.1f} min")
    for name, status in outcomes.items():
        print(f"  {status:9} {name}")

    _record(args.dry_run, outcomes, failed=failed, skipped=skipped)

    if failed or skipped:
        print(f"\n[{JOB}] {len(failed)} failed, {len(skipped)} skipped — the screener is "
              "showing older data for those parts, not wrong data")
        return 1

    if idle:
        print(f"\n[{JOB}] no new prices: recompute steps idle, verify ran")
    else:
        print(f"\n[{JOB}] all steps completed")
    return 0


def _record(
    dry_run: bool, outcomes: dict[str, str], *, failed: list[str], skipped: list[str]
) -> None:
    """Write an ingestion_runs row for every run, not only the bad ones.

    Recording failures alone leaves the last row permanently red: a later good
    run writes nothing, so the health check keeps reporting a fault that was
    fixed hours ago, and a check that cries wolf is one you learn to skip.

    A step that never started also writes nothing of its own, and an absent row
    is indistinguishable from a job that was never scheduled — which is the
    whole reason this table exists.
    """
    try:
        db = Db(force_dry_run=dry_run)
        run_id = db.start_run(JOB)
        db.finish_run(
            run_id,
            status="partial" if (failed or skipped) else "ok",
            symbols_ok=sum(1 for s in outcomes.values() if s in ("ok", "tolerated")),
            symbols_failed=len(failed) + len(skipped),
            errors=[{"symbol": name, "error": status}
                    for name, status in outcomes.items() if status in ("failed", "skipped")],
            notes="; ".join(f"{n}={s}" for n, s in outcomes.items()),
        )
    except Exception:  # noqa: BLE001 - never let bookkeeping mask the real failure
        pass


if __name__ == "__main__":
    sys.exit(main())
