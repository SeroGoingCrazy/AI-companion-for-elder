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

## Languages

The interface ships in English and Simplified Chinese (`config/i18n/*.yaml`). English is the
default; a switch in each header sets a `lang` cookie, and `?lang=zh` pins it for a demo link
without touching the cookie.

Two things deliberately do not follow that switch. The companion's replies and the daily
summary follow the language the elder actually spoke, which the prompt handles. So does the
line spoken back to her when speech could not be understood: a Chinese speaker hears Chinese
even when her family has set the dashboard to English.

Symptom names are not in the i18n files. `config/symptoms.yaml` already carries `en` and `zh`
for every canonical and stays the single source of truth; the API returns both and the page
picks one. Stored alert titles stay in English as the record of what happened, and are
re-derived for display from the symptom the alert points at.

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

### Demo screenshots

`demo/shots/` holds the pitch-deck shots, and they are reproducible rather than hand-taken:
the script replays the DEV_SPEC demo script against a running server and captures the elder
app at phone size and the dashboard at laptop size, including the urgent alert arriving live.

```bash
uv run --with playwright python scripts/demo_shots.py
```

It drives the copy of Chrome already on the machine, so there is no browser download. Run it
against a fresh `data/app.db` for clean shots.

## Fall detection (fall-mcp)

Needs the optional vision dependencies, the pose model and the demo clips
(UR Fall Detection Dataset, see [demo/videos/README.md](demo/videos/README.md)):

```bash
uv sync --extra vision
uv run --extra vision python scripts/fetch_demo_media.py   # models/ + demo/videos/
```

`uv run` re-syncs to the default dependency set and removes the vision extra, so the
`--extra vision` is needed on every vision command, not just the install.

Run it next to the web app (`uv run elder-web`):

```bash
uv run --extra vision python -m fall_detector.server                          # :8001, loops demo/videos/walk.mp4
uv run --extra vision python -m fall_detector.ctl play demo/videos/fall_01.mp4  # demo step: play the fall once
uv run --extra vision python -m fall_detector.ctl play demo/videos/walk.mp4 --loop
```

The dashboard's fall panel shows the live view (`:8001/stream`); a confirmed fall posts an alert with a
snapshot to the dashboard. Use a camera with `--source 0` (or `FALL_SOURCE=0`); on Apple Silicon set
`FALL_DEVICE=mps`. Check a single clip without the service:
`uv run --extra vision python -m fall_detector --source demo/videos/lie_down.mp4 --show`.

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
scripts/                fetch_demo_media.py (model + clips), make_icons.py (PWA icons),
                        demo_shots.py (pitch-deck screenshots)
docs/                   mcp_desktop.md (Claude Desktop setup)
```
