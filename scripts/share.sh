#!/usr/bin/env bash
# Put the running demo on an HTTPS address and print a QR code for it.
#
#   scripts/share.sh            English
#   scripts/share.sh zh         Chinese
#   scripts/share.sh zh --lhr   force localhost.run instead of Cloudflare
#
# With cloudflared installed (brew install cloudflared) the tunnel is a Cloudflare quick
# tunnel: no account, and it stays up for as long as this runs. Without it, localhost.run,
# whose free tunnels drop after a while and come back on a new address.
#
# A phone cannot install the app from a LAN address: service workers need a secure
# context, so http://192.168.x.x will not register one and neither iOS nor Android will
# offer to install. An HTTPS tunnel is the only way to see the real thing on a phone.
#
# This puts the demo on the public internet for as long as it runs. The seeded elder is
# fictional. Ctrl-C closes the tunnel.
set -euo pipefail
cd "$(dirname "$0")/.."

LANG_CODE="en"
USE_LHR=0
for arg in "$@"; do
  case "$arg" in
    --lhr) USE_LHR=1 ;;
    *) LANG_CODE="$arg" ;;
  esac
done
command -v cloudflared >/dev/null || USE_LHR=1
PORT="${PORT:-8000}"

curl -sf -o /dev/null "http://127.0.0.1:${PORT}/healthz" || {
  echo "Nothing is running on :${PORT} — start it with ./scripts/demo_up.sh first." >&2
  exit 1
}

LOG=$(mktemp)
cleanup() { kill %1 2>/dev/null || true; rm -f "$LOG"; }
trap cleanup EXIT INT TERM

if [[ $USE_LHR -eq 0 ]]; then
  echo "opening a Cloudflare tunnel…"
  cloudflared tunnel --no-autoupdate --url "http://localhost:${PORT}" > "$LOG" 2>&1 &
  PATTERN='https://[a-z0-9-]+\.trycloudflare\.com'
  READY='Registered tunnel connection'
else
  echo "opening a localhost.run tunnel…"
  # Cloudflare's quick tunnels are blocked on some networks; localhost.run is plain ssh.
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ServerAliveInterval=20 -o ServerAliveCountMax=6 -o ExitOnForwardFailure=yes \
      -R 80:localhost:"$PORT" nokey@localhost.run > "$LOG" 2>&1 &
  PATTERN='https://[a-z0-9.-]+\.lhr\.life'
  READY='lhr.life'
fi

URL=""
for _ in $(seq 1 25); do
  sleep 2
  if grep -q "$READY" "$LOG"; then
    URL=$(grep -oE "$PATTERN" "$LOG" | head -1 || true)
  fi
  [ -n "$URL" ] && break
done
[ -n "$URL" ] || {
  echo "tunnel did not come up:" >&2; tail -5 "$LOG" >&2
  [[ $USE_LHR -eq 0 ]] && echo "Cloudflare may be blocked here; try: scripts/share.sh $LANG_CODE --lhr" >&2
  exit 1
}

ELDER="${URL}/elder?lang=${LANG_CODE}"
FAMILY="${URL}/family?lang=${LANG_CODE}"

printf '\n\033[1mScan on a phone\033[0m\n\n'
for pair in "Elder app|$ELDER" "Family dashboard|$FAMILY"; do
  name="${pair%%|*}"; link="${pair#*|}"
  printf '  %s\n  %s\n\n' "$name" "$link"
  if command -v qrencode >/dev/null; then
    qrencode -t ANSIUTF8 -m 1 "$link"
  fi
done
command -v qrencode >/dev/null || echo "  (brew install qrencode to print QR codes here)"

cat <<TXT

  iPhone: open in Safari, then Share -> Add to Home Screen.
  Android: open in Chrome, then the menu -> Install app.

  The address changes every time this runs, so open it fresh on demo day.
  Keep this terminal open (and the Mac awake): the link lives as long as this does.
  Ctrl-C closes the tunnel.
TXT
wait
