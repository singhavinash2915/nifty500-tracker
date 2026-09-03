"""Job: check that the setup is actually wired up.

    python -m n500.jobs.doctor

Answers the question "is this thing working?" without needing to read four log
files. Checks credentials, the schema, every table, whether the data is fresh,
and — the one most easily missed — whether any job died half way and left an
`ingestion_runs` row still marked running.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from ..config import settings
from ..db import Db

JOB = "doctor"

EXPECTED_TABLES = [
    "stocks", "index_membership", "prices_daily", "technicals_daily",
    "fundamentals_q", "fundamentals_y", "shareholding", "company_ratios",
    "index_prices", "support_zones", "zone_events", "scores_daily",
    "ts_setups", "fundamental_scores", "watchlist", "positions", "alerts",
    "ingestion_runs",
]

# A table nobody has loaded yet is a different problem from a stale one.
FRESHNESS_DAYS = {"prices_daily": 5, "scores_daily": 5, "fundamentals_y": 40}

OK, WARN, BAD = "ok  ", "warn", "FAIL"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the setup")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    problems = 0
    print(f"{'=' * 62}\n  SETUP CHECK\n{'=' * 62}")

    print(f"  [{OK if settings.has_supabase else WARN}] credentials "
          f"{'found' if settings.has_supabase else 'absent — running against data/dryrun/'}")
    print(f"  [{OK}] schema         {settings.db_schema}")

    db = Db(force_dry_run=args.dry_run)
    if db.dry_run:
        print(f"  [{WARN}] mode           dry run; nothing below touches Supabase")

    print("\n  tables")
    counts: dict[str, int] = {}
    for table in EXPECTED_TABLES:
        try:
            rows = db.select(table, "*")
            counts[table] = len(rows)
            status = OK if rows else WARN
            note = "" if rows else "  (empty)"
            print(f"  [{status}] {table:20} {len(rows):>8,} rows{note}")
        except Exception as exc:  # noqa: BLE001
            problems += 1
            message = str(exc)
            print(f"  [{BAD}] {table:20} {message[:90]}")
            if "PGRST106" in message or "Invalid schema" in message:
                print(f"\n  >>> The `{settings.db_schema}` schema is not exposed to the API.")
                print("      Dashboard -> Settings -> API -> Exposed schemas -> add it.")
                return 1

    print("\n  freshness")
    for table, limit in FRESHNESS_DAYS.items():
        if not counts.get(table):
            print(f"  [{WARN}] {table:20} nothing loaded yet")
            continue
        column = "date" if table != "fundamentals_y" else "period_end"
        frame = pd.DataFrame(db.select(table, f"{column}"))
        if frame.empty or column not in frame:
            continue
        latest = pd.to_datetime(frame[column], errors="coerce").max()
        if pd.isna(latest):
            continue
        age = (date.today() - latest.date()).days
        status = OK if age <= limit else WARN
        if age > limit:
            problems += 1
        print(f"  [{status}] {table:20} newest {latest.date()} ({age}d old, "
              f"expected within {limit}d)")

    # A job that died leaves status='running' with no finished_at. An absent
    # row and a stuck row look identical from the data alone, which is why the
    # audit table exists.
    print("\n  recent jobs")
    runs = pd.DataFrame(db.select("ingestion_runs", "*"))
    if runs.empty:
        print(f"  [{WARN}] no ingestion_runs rows — nothing has run against this database")
    else:
        runs["started_at"] = pd.to_datetime(runs["started_at"], errors="coerce", utc=True)
        recent = runs.sort_values("started_at").tail(12)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
        for _, row in recent.iterrows():
            status_text = row.get("status")
            stuck = status_text == "running" and row["started_at"] < cutoff
            mark = BAD if stuck or status_text == "failed" else (
                WARN if status_text == "partial" else OK
            )
            if mark == BAD:
                problems += 1
            suffix = "  <- started but never finished" if stuck else ""
            print(f"  [{mark}] {str(row.get('job')):24} {status_text:8} "
                  f"{row['started_at']:%Y-%m-%d %H:%M}{suffix}")

    print()
    if problems:
        print(f"  {problems} thing(s) need attention.")
        return 1
    print("  Everything checks out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
