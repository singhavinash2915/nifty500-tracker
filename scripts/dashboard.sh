#!/bin/bash
#
# Open the tracker on this Mac: database, dashboard, browser. Safe to run
# again at any time; whatever is already up is reused.
#
#   ./scripts/dashboard.sh
#
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=5175
URL="http://localhost:$PORT/"
. "$REPO/scripts/local-stack.sh"

echo "database..."
ensure_local_stack || exit 1

if curl -s -o /dev/null --max-time 2 "$URL"; then
  echo "dashboard already running"
else
  echo "starting the dashboard on port $PORT"
  mkdir -p "$REPO/data/logs"
  nohup npm run dev --prefix "$REPO/web" -- --port "$PORT" --strictPort \
    > "$REPO/data/logs/dashboard.log" 2>&1 &
  for _ in $(seq 1 30); do
    curl -s -o /dev/null --max-time 2 "$URL" && break
    sleep 1
  done
  curl -s -o /dev/null --max-time 2 "$URL" || {
    echo "dashboard did not start; see data/logs/dashboard.log"
    exit 1
  }
fi

open "$URL"
echo "open at $URL"
