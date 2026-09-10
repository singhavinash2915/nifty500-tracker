#!/bin/bash
#
# Bring the tracker back after the Supabase egress restriction lifts.
#
# The free-plan quota resets at the start of the billing cycle — 1 October 2026
# for this organization — and the restriction clears within about a day of it.
# The timers were stopped for the outage rather than left to fail nightly: a
# pipeline that cannot write is not doing anything, and three weeks of failure
# notifications about a known problem is how an alert stops being read.
#
# Nothing is lost in the gap. NSE bhavcopy files are immutable and published
# for past dates, so the missing sessions are simply fetched. --days 35 covers
# 1 September onward with room to spare; already-cached days cost no network.
#
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO/ingestion" || exit 1

echo "1/4  checking the restriction has actually lifted"
if ! "$REPO/.venv/bin/python" - <<'PY'
import sys
from n500.db import Db
try:
    Db().count("stocks")
    print("     service restored")
except Exception as exc:
    print(f"     still restricted: {str(exc)[:120]}")
    sys.exit(1)
PY
then
  echo
  echo "Stopping here. Re-run this once the quota has reset."
  exit 1
fi

echo "2/4  backfilling the sessions missed during the outage"
PYTHONUNBUFFERED=1 "$REPO/.venv/bin/python" -m n500.jobs.load_prices --days 35

echo "3/4  rebuilding everything downstream"
PYTHONUNBUFFERED=1 "$REPO/.venv/bin/python" -m n500.jobs.run_nightly --days 35

echo "4/4  re-enabling the timers"
sudo systemctl enable --now n500-nightly.timer n500-live.timer
systemctl list-timers --all 'n500*' --no-pager
