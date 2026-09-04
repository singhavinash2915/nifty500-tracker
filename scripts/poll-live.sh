#!/bin/bash
# Intraday index poll, run on a short interval by launchd.
#
# The job decides for itself whether the market is open, so launchd can fire it
# all day and it costs nothing outside the session.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO/ingestion" || exit 1
"$REPO/.venv/bin/python" -m n500.jobs.poll_live --quiet >> "$REPO/data/logs/live-$(date +%Y-%m-%d).log" 2>&1
