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
        Step("technicals", "compute_technicals", common, needs=("prices", "index")),
        Step("fundamental_scores", "compute_fundamental_scores", common,
             needs=("universe",), tolerate_failure=True),
        Step("zones", "compute_zones", common, needs=("prices",)),
        Step("scores", "compute_scores", common, needs=("technicals", "zones")),
        Step("snapshot", "export_snapshot", common, needs=("scores",)),
        Step("positions", "compute_positions", common, needs=("scores",)),
        Step("alerts", "compute_alerts", [*common, "--quiet"], needs=("scores",)),
    ]
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
    args = parser.parse_args(argv)

    steps = plan(args.days, dry_run=args.dry_run, skip_fundamentals=args.skip_fundamentals)
    outcomes: dict[str, str] = {}
    started = time.monotonic()

    print(f"[{JOB}] starting {datetime.now():%Y-%m-%d %H:%M:%S} — {len(steps)} steps")

    for step in steps:
        blockers = [n for n in step.needs if outcomes.get(n) not in ("ok", "tolerated")]
        if blockers:
            outcomes[step.name] = "skipped"
            print(f"[{JOB}] SKIP {step.name}: needs {', '.join(blockers)}, which did not succeed")
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
