#!/usr/bin/env bash
# Start the whole demo: the web app, and fall detection if its model is present.
#
#   scripts/demo_up.sh            offline demo, no API key needed (LLM_PROVIDER=mock)
#   scripts/demo_up.sh --openai   real models, requires OPENAI_API_KEY in .env
#
# Ctrl-C stops everything it started.
set -euo pipefail
cd "$(dirname "$0")/.."

WITH_OPENAI=0
[[ "${1:-}" == "--openai" ]] && WITH_OPENAI=1

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
port_busy() { lsof -ti:"$1" >/dev/null 2>&1; }

say "1/4  Dependencies"
uv sync --quiet
VISION=0
if [[ -f models/yolo11n-pose.pt && -n "$(ls demo/videos/*.mp4 2>/dev/null)" ]]; then
  # `uv run` re-syncs to the default set and drops the extra, so every vision command
  # below carries --extra vision and the sync has to happen up front.
  uv sync --quiet --extra vision && VISION=1
fi

say "2/4  Configuration"
[[ -f .env ]] || cp .env.example .env
if [[ $WITH_OPENAI -eq 1 ]]; then
  grep -q '^OPENAI_API_KEY=.\+' .env || { echo "Set OPENAI_API_KEY in .env first." >&2; exit 1; }
  sed -i '' 's/^LLM_PROVIDER=.*/LLM_PROVIDER=openai/' .env
  echo "     real models"
else
  sed -i '' 's/^LLM_PROVIDER=.*/LLM_PROVIDER=mock/' .env
  echo "     offline (mock replies, no API key used)"
fi

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

say "3/4  Services"
if port_busy 8000; then
  echo "     :8000 already in use, leaving it alone"
else
  uv run --no-sync elder-web >/tmp/sunny-web.log 2>&1 &
  PIDS+=($!); echo "     web app   :8000"
fi

if [[ $VISION -eq 1 ]]; then
  if port_busy 8001; then
    echo "     :8001 already in use, leaving it alone"
  else
    uv run --no-sync --extra vision python -m fall_detector.server >/tmp/sunny-fall.log 2>&1 &
    PIDS+=($!); echo "     fall-mcp  :8001"
  fi
else
  echo "     fall detection skipped (run scripts/fetch_demo_media.py to enable it)"
fi

for _ in $(seq 1 40); do
  sleep 1
  curl -sf -o /dev/null http://127.0.0.1:8000/healthz && break
done
curl -sf -o /dev/null http://127.0.0.1:8000/healthz || { echo "web app did not come up; see /tmp/sunny-web.log" >&2; exit 1; }

say "4/4  Ready"
cat <<TXT
     Elder app   http://127.0.0.1:8000/elder
     Dashboard   http://127.0.0.1:8000/family
     Chinese     add ?lang=zh to either

     A phone needs HTTPS to install the app; a LAN address will not do:
       cloudflared tunnel --url http://127.0.0.1:8000
       ssh -R 80:localhost:8000 nokey@localhost.run     # where Cloudflare is blocked

     Ctrl-C stops everything.
TXT
wait
