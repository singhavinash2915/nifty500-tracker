# Sourced, not run. Brings up the local Supabase stack when .env points at it.
#
# Used by the nightly and by the dashboard launcher. Both can start from a
# cold Mac: launchd runs jobs with a bare PATH that does not include Homebrew,
# and nothing guarantees Docker Desktop is open at 19:15. On the cloud box
# .env points at a hosted project and this does nothing.

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

uses_local_stack() {
  grep -qE '^SUPABASE_URL=http://(127\.0\.0\.1|localhost)' "$REPO/.env" 2>/dev/null
}

# Asks the engine directly, with a hard time limit. `docker info` has none and
# hung for ten minutes against an app caught half-way through quitting, which
# held the whole nightly with it.
engine_up() {
  [ "$(curl -s --max-time 3 --unix-socket "$HOME/.docker/run/docker.sock" \
        http://localhost/_ping 2>/dev/null)" = "OK" ]
}

wait_for_engine() {
  for _ in $(seq 1 "$1"); do
    engine_up && return 0
    sleep 3
  done
  return 1
}

ensure_local_stack() {
  uses_local_stack || return 0

  if ! engine_up; then
    echo "starting Docker Desktop"
    # `docker desktop start` revives an app that is running but whose engine
    # is not, which `open -a Docker` only brings to the front. Bounded, so a
    # wedged app fails the run rather than stalling it.
    docker desktop start --timeout 180 >/dev/null 2>&1 || open -a Docker
    if ! wait_for_engine 60; then
      # An app whose engine has died reports "running" and ignores `start`.
      # Seen after Docker was quit part-way through shutting down; a restart
      # cleared it in 12 seconds.
      echo "engine not answering; restarting Docker Desktop"
      docker desktop restart --timeout 180 >/dev/null 2>&1
      wait_for_engine 60 || { echo "Docker did not come up after a restart"; return 1; }
    fi
  fi

  # Idempotent: exits 0 at once when the stack is already running. Retried,
  # because Docker answers its ping a few seconds before it can run
  # containers: a cold start from launchd failed here and the same command
  # succeeded by hand moments later.
  local out="" started=1 attempt
  for attempt in 1 2 3; do
    if out="$(cd "$REPO" && supabase start 2>&1)"; then started=0; break; fi
    sleep 10
  done
  if [ "$started" -ne 0 ]; then
    echo "supabase start failed three times; last output:"
    # Its success output prints keys, so filter before logging anything.
    printf '%s\n' "$out" | grep -v -i -E "key|secret|jwt|token" | tail -5
    return 1
  fi

  # The containers report started before the API accepts requests.
  for _ in $(seq 1 30); do
    curl -s -o /dev/null --max-time 3 http://127.0.0.1:54321/rest/v1/ && return 0
    sleep 2
  done
  echo "local API did not answer on 127.0.0.1:54321"
  return 1
}
