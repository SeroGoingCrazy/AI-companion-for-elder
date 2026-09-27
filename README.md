# AI Companion for Elder

An AI companion agent for older adults living alone, plus a care dashboard for their family.

- **Elder app** (`/elder`): voice-first chat companion — hold to talk, replies are read aloud.
- **Symptom log**: symptoms mentioned in casual conversation are extracted automatically; red-flag symptoms alert the family in real time.
- **Family dashboard** (`/family`): daily summary, symptom timeline, alerts, live fall-detection view.
- **fall-mcp**: YOLO11-pose fall detection exposed as an MCP server (usable from the dashboard agent, Claude Desktop, Cursor), with real-time push alerts.

> This product does not provide medical diagnosis.

See [DEV_SPEC.md](DEV_SPEC.md) for the full design and development plan.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
cp .env.example .env   # then set OPENAI_API_KEY
uv run pytest -q
```

## Install on a phone (PWA)

Both surfaces are installable, so the demo runs full screen with no browser chrome:
the elder installs **Sunny** (opens at `/elder`), the family installs **Sunny Care**
(opens at `/family`). A service worker precaches the shell, so the app still opens and
renders if the venue wifi drops mid-demo. Live data — chat turns, the alert stream, TTS
audio, fall snapshots — always goes to the network and is never cached.

On the machine running the server, `http://127.0.0.1:8000/elder` installs directly.

**From an actual phone, the LAN address will not work.** Service workers need a secure
context, and `http://10.x.x.x:8000` is not one: no worker registers, and neither iOS nor
Android will offer to install. Put an HTTPS URL in front of it instead:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

That prints a public `https://….trycloudflare.com` address. Open it on the phone, then
**Share → Add to Home Screen** (iOS, Safari only) or **⋮ → Install app** (Android Chrome).

> The tunnel puts the demo on the public internet for as long as it runs. The seeded elder
> is fictional, but stop the tunnel when the demo is over.

Icons are generated, not hand-drawn — rerun `scripts/make_icons.py` if the palette changes.

## Fall detection (fall-mcp)

Needs the optional vision dependencies, the pose model and the demo clips
(UR Fall Detection Dataset, see [demo/videos/README.md](demo/videos/README.md)):

```bash
uv sync --extra vision
uv run python scripts/fetch_demo_media.py      # models/yolo11n-pose.pt + demo/videos/*.mp4
```

Run it next to the web app (`uv run elder-web`):

```bash
uv run python -m fall_detector.server                          # :8001, loops demo/videos/walk.mp4
uv run python -m fall_detector.ctl play demo/videos/fall_01.mp4  # demo step: play the fall once
uv run python -m fall_detector.ctl play demo/videos/walk.mp4 --loop
```

The dashboard's fall panel shows the live view (`:8001/stream`); a confirmed fall posts an alert with a
snapshot to the dashboard. Use a camera with `--source 0` (or `FALL_SOURCE=0`); on Apple Silicon set
`FALL_DEVICE=mps`. Check a single clip without the service:
`uv run python -m fall_detector --source demo/videos/lie_down.mp4 --show`.

MCP: Streamable HTTP at `http://127.0.0.1:8001/mcp`, or stdio for Claude Desktop — see
[docs/mcp_desktop.md](docs/mcp_desktop.md).

## Layout

```
config/                 settings, symptom catalog, prompts
src/elder_companion/    main web app: chat, voice, symptom log, alerts, family dashboard
src/fall_detector/      fall-mcp: pose estimation, fall state machine, MCP tools, MJPEG stream
tests/                  unit / integration tests (offline, mock LLM)
eval/                   symptom extraction eval set
demo/                   demo videos and script
scripts/                fetch_demo_media.py (model + clips), make_icons.py (PWA icons)
docs/                   mcp_desktop.md (Claude Desktop setup)
```
