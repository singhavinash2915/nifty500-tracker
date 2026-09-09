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

# A failure that nobody hears about is a failure that gets acted on. The 19:15
# run on 4 September failed at its first step, skipped the other nine, and sat
# silently until somebody thought to ask — while the app went on showing the
# previous day's ranking as though it were current.
#
# osascript rather than a notifier package: it is already on every Mac, it needs
# no credentials, and a dependency that has to be installed is one that will be
# missing on the machine where this matters.
#
# On the Linux box there is no desktop to notify and this silently does nothing,
# which is correct — the channel there is the banner in the app, driven by the
# `verify` row in ingestion_runs, and that one reaches you wherever you are
# rather than only at the machine.
notify() {
  local title="$1" message="$2"
  osascript -e "display notification \"${message//\"/}\" with title \"${title//\"/}\"" \
    >/dev/null 2>&1 || true
}

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') starting ==="
  # Unbuffered: redirected to a file, Python buffers stdout in 8KB blocks,
  # so a run in progress shows nothing and a run that hangs shows nothing
  # about where. The log is for watching, which needs it written as it goes.
  PYTHONUNBUFFERED=1 "$REPO/.venv/bin/python" -m n500.jobs.run_nightly --days 10 $EXTRA
  STATUS=$?
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') exit $STATUS ==="
} >> "$LOG" 2>&1

if [ "$STATUS" -ne 0 ]; then
  # The last line naming a step is the most useful thing to put in four inches
  # of notification — "FAILED universe" says more than "exit 1".
  DETAIL="$(grep -E 'FAILED|check\(s\) failed' "$LOG" | tail -1 | cut -c1-120)"
  notify "Nifty 500 tracker — nightly failed" "${DETAIL:-see $LOG}"
fi

exit $STATUS
