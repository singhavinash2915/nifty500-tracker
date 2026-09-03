#!/bin/bash
#
# Nightly entry point, called by launchd.
#
# launchd starts jobs with a bare environment and the wrong working directory,
# so both are set explicitly here rather than assumed. Output goes to a dated
# log so a failure three weeks ago is still readable.
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/nightly-$(date +%Y-%m-%d).log"

cd "$REPO/ingestion" || exit 1

# Fundamentals change four times a year. Refetching 500 pages every night is
# scrape volume for no information and the fastest way to get blocked, so they
# refresh weekly — on Sundays — and the page cache covers the rest.
EXTRA=""
if [ "$(date +%u)" != "7" ]; then
  EXTRA="--skip-fundamentals"
fi

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') starting ==="
  "$REPO/.venv/bin/python" -m n500.jobs.run_nightly --days 10 $EXTRA
  STATUS=$?
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') exit $STATUS ==="
  exit $STATUS
} >> "$LOG" 2>&1
