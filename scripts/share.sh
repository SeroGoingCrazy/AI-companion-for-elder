#!/usr/bin/env bash
# Put the running demo on an HTTPS address and print a QR code for it.
#
#   scripts/share.sh            English
#   scripts/share.sh zh         Chinese
#
# A phone cannot install the app from a LAN address: service workers need a secure
# context, so http://192.168.x.x will not register one and neither iOS nor Android will
# offer to install. An HTTPS tunnel is the only way to see the real thing on a phone.
#
# This puts the demo on the public internet for as long as it runs. The seeded elder is
# fictional. Ctrl-C closes the tunnel.
set -euo pipefail
cd "$(dirname "$0")/.."

LANG_CODE="${1:-en}"
PORT="${PORT:-8000}"

curl -sf -o /dev/null "http://127.0.0.1:${PORT}/healthz" || {
  echo "Nothing is running on :${PORT} — start it with ./scripts/demo_up.sh first." >&2
  exit 1
}

LOG=$(mktemp)
cleanup() { kill %1 2>/dev/null || true; rm -f "$LOG"; }
trap cleanup EXIT INT TERM

echo "opening a tunnel…"
# localhost.run needs no account. Cloudflare's quick tunnels are blocked on some networks,
# which is why this is the default rather than `cloudflared tunnel`.
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -o ServerAliveInterval=20 -o ExitOnForwardFailure=yes \
    -R 80:localhost:"$PORT" nokey@localhost.run > "$LOG" 2>&1 &

URL=""
for _ in $(seq 1 25); do
  sleep 2
  URL=$(grep -oE 'https://[a-z0-9.-]+\.lhr\.life' "$LOG" | head -1 || true)
  [ -n "$URL" ] && break
done
[ -n "$URL" ] || { echo "tunnel did not come up:" >&2; tail -5 "$LOG" >&2; exit 1; }

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
  Ctrl-C closes the tunnel.
TXT
wait
