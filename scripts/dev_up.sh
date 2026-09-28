#!/usr/bin/env bash
# Development: (re)start the web app and fall detection together, with both logs here.
#
#   scripts/dev_up.sh
#
# Unlike demo_up.sh it leaves .env alone (LLM_PROVIDER, FALL_CAMERA, ... stay as you set
# them) and restarts whatever is already on the two ports instead of skipping them.
# Fall detection starts idle; pick Camera or Demo video on the dashboard's Camera tab.
# Run it from your own terminal app: on macOS the camera permission belongs to the app
# that launched the process.
#
# Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
FALL_PORT="${FALL_MCP_PORT:-8001}"
BASE="http://127.0.0.1:${PORT}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }

free_port() {
  local pids
  pids="$(lsof -ti tcp:"$1" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -z "$pids" ]] && return
  say "Stopping what is on :$1 ($(echo $pids | tr '\n' ' '))"
  kill $pids 2>/dev/null || true
  for _ in $(seq 1 20); do
    lsof -ti tcp:"$1" -sTCP:LISTEN >/dev/null 2>&1 || return 0
    sleep 0.25
  done
  kill -9 $pids 2>/dev/null || true
}

# `uv run` re-syncs to the default set and drops the vision extra, so sync once with it
# and start both with --no-sync.
say "Syncing dependencies"
uv sync --quiet --extra vision

free_port "$PORT"
free_port "$FALL_PORT"

PIDS=()
cleanup() {
  trap - EXIT INT TERM
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  # The pids above are the log pipelines; the servers under them are found by port.
  free_port "$PORT"
  free_port "$FALL_PORT"
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Each service's output, prefixed so the two streams can be told apart.
run() {
  local tag="$1"; shift
  ( "$@" 2>&1 | sed -u "s/^/[$tag] /" ) &
  PIDS+=($!)
}

run web  uv run --no-sync elder-web
run fall uv run --no-sync --extra vision python -m fall_detector.server --no-autostart

for _ in $(seq 1 40); do
  curl -sf -o /dev/null "$BASE/healthz" && break
  sleep 1
done
curl -sf -o /dev/null "$BASE/healthz" || { echo "web app did not come up (see [web] above)" >&2; exit 1; }

say "Ready"
cat <<TXT
  Elder app    $BASE/elder
  Dashboard    $BASE/family      (add ?lang=zh for Chinese)
  Fall-mcp     http://127.0.0.1:$FALL_PORT
  Ctrl-C stops both.
TXT
wait
