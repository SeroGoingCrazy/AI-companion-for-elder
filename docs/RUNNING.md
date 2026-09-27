# Running it

Two commands: one to start it, one to put it on a phone.

## On this machine

```bash
./scripts/demo_up.sh
```

That syncs dependencies, writes `.env` if it is missing, starts the web app, starts fall
detection if its model is on disk, waits until the app answers, and prints the addresses.
Ctrl-C stops everything it started.

| | |
|---|---|
| Elder app | <http://127.0.0.1:8000/elder> |
| Family dashboard | <http://127.0.0.1:8000/family> |
| Chinese | add `?lang=zh` to either |

**No API key is needed.** The script defaults to `LLM_PROVIDER=mock`, which answers from
`config/mock_llm.yaml` instead of calling a model, so a fresh clone runs offline.

Two things follow from that, and both are worth knowing before you show anyone:

- Replies are canned. Say something the rules do not match and you get the same generic
  line every time. That is the mock, not a bug.
- The voice is the browser's own speech synthesis, which sounds robotic. Real speech needs
  the API.

For real conversation and real speech, put a key in `.env` and run:

```bash
./scripts/demo_up.sh --openai
```

If port 8000 is taken, `PORT=8100 ./scripts/demo_up.sh` moves everything, including the
addresses it prints.

## On a phone

```bash
./scripts/share.sh          # English
./scripts/share.sh zh       # Chinese
```

It opens an HTTPS tunnel and prints a QR code for each app, straight into the terminal.
Scan, and on iPhone use **Share → Add to Home Screen** in Safari; on Android, **⋮ →
Install app** in Chrome. The app then opens full screen with its own icon and no browser
chrome.

**A LAN address will not work**, and this is the one thing that reliably ruins a demo.
Service workers need a secure context, so `http://192.168.x.x:8000` registers no worker
and neither iOS nor Android offers to install. The tunnel is not a convenience; it is the
only way to see the real thing on a phone.

Two caveats:

- **The address changes every run.** Open it fresh on the day. Do not photograph a QR code
  the night before.
- **The tunnel is public** while it runs. The seeded elder is fictional, but close it when
  you are done. Ctrl-C does that.

WeChat's in-app browser cannot install anything and always shows its own toolbar. If you
send the link to someone, tell them to open it in Safari or Chrome.

## Fall detection

Skipped unless its model and clips are present. To enable it:

```bash
uv sync --extra vision
uv run --extra vision python scripts/fetch_demo_media.py
```

About 1GB. `demo_up.sh` picks it up automatically on the next run.

`uv run` re-syncs to the default dependency set and silently drops the vision extra, so
every vision command needs `--extra vision` — even right after `uv sync --extra vision`
succeeded.

## When something is wrong

| What you see | What it is |
|---|---|
| The same reply to everything | Mock mode. Expected without an API key. |
| Robotic voice | Browser speech synthesis. Real TTS needs the API. |
| Camera panel says offline | fall-mcp is not running, or its model was never fetched. |
| A phone will not offer to install | You are on a LAN address, or in WeChat's browser. |
| The tunnel address stopped working | Free tunnels drop. Run `share.sh` again for a new one. |
| `ModuleNotFoundError: cv2` | A `uv run` dropped the vision extra. Add `--extra vision`. |

Logs are at `/tmp/sunny-web.log` and `/tmp/sunny-fall.log`.
