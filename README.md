# AI Companion for Elder

An AI companion agent for older adults living alone, plus a care dashboard for their family.
Two apps, one system: what she says on her phone reaches her family in seconds.

![Sunny and Sunny Care](docs/images/hero.png)

- **Elder app** (`/elder`): voice-first chat companion — hold to talk, replies are read aloud.
- **Symptom log**: symptoms mentioned in casual conversation are extracted automatically; red-flag symptoms alert the family in real time.
- **Companion memory**: remembers small life details ("I'll repot my orchid this week") and asks about them in a later greeting.
- **Parent-controlled privacy**: "keep this between us" hides that part from every family view, while the companion still remembers it; urgent safety alerts (a fall, chest pain) always go through, and the parent is told so up front.
- **Family dashboard** (`/family`): daily summary, symptom timeline, alerts, live fall-detection view, "on her mind" care list, and sibling sharing (`?member=ben`, "I'll handle this").
- **Reminders**: family sets a reminder (a daily pill, a one-off "did you book the eye doctor?"); the companion raises it in her next chat, in the family's own wording, and her answer shows up on the dashboard as 7-day adherence. It never adds a dose or medical advice.
- **Reports**: a printable weekly report (`/family/report/weekly`: mood trend, most-discussed topics, symptom trends and reminder adherence), a doctor one-pager (`/family/doctor`, built without an LLM) and a memoir of her stories (`/family/memoir`).
- **fall-mcp**: YOLO11-pose fall detection exposed as an MCP server (usable from the dashboard agent, Claude Desktop, Cursor), with real-time push alerts.

Both surfaces install to a phone home screen, in English or Simplified Chinese.

> This product does not provide medical diagnosis.

See [DEV_SPEC.md](DEV_SPEC.md) for the full design and development plan.

## Try it in two minutes

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12. No API key needed: the demo runs
offline against canned replies, so a fresh clone works on a plane.

```bash
./scripts/demo_up.sh
```

Open <http://127.0.0.1:8000/elder> and <http://127.0.0.1:8000/family>, or add `?lang=zh` to
either for Chinese. Ctrl-C stops everything. For real models instead of canned ones, put
`OPENAI_API_KEY` in `.env` and run `./scripts/demo_up.sh --openai`.

## The two apps

![The elder's app](docs/images/elder-app.png)

The elder surface is one screen with one control. Body type is 23px, the talk bar is 84px
tall and every colour pair clears 4.5:1 — above the floors in WeChat's Care Mode spec and
the MIIT accessibility standard. The control is a bar rather than a disc because that is
the shape this audience already knows from voice messages.

![The family's app](docs/images/family-app.png)

The family surface answers five questions on five tabs: how is she, what happened, what is
she carrying and what should the companion bring up, let me look, what did she say. An
urgent alert jumps to Alerts on arrival and badges the tab.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
cp .env.example .env   # then set OPENAI_API_KEY
uv run pytest -q
```

Run the app offline with seeded demo data (6 days of history, family members Amy and Ben):

```bash
LLM_PROVIDER=mock uv run python -m elder_companion.seed --reset --with-history
LLM_PROVIDER=mock uv run elder-web     # http://127.0.0.1:8000/elder and /family
```

Extraction evals (need `OPENAI_API_KEY`):

```bash
uv run python eval/run_extraction_eval.py
uv run python eval/run_memory_eval.py
```

## Fall detection (fall-mcp)

Needs the optional vision dependencies, the pose model and the demo clips
(UR Fall Detection Dataset, see [demo/videos/README.md](demo/videos/README.md)):

```bash
uv sync --extra vision
uv run python scripts/fetch_demo_media.py      # models/yolo11n-pose.pt + demo/videos/*.mp4
```

Run it next to the web app (`uv run elder-web`):

```bash
uv run python -m fall_detector.server                          # :8001, loops demo/videos/demo.m3u
uv run python -m fall_detector.ctl play demo/videos/fall_01.mp4  # demo step: play the fall once
uv run python -m fall_detector.ctl play demo/videos/walk.mp4 --loop
```

The dashboard's fall panel shows the live view (`:8001/stream`); a confirmed fall posts an alert with a
snapshot to the dashboard. The panel's **Camera / Demo video / Pause** buttons switch the source; Demo video
plays every scenario in [demo/videos/demo.m3u](demo/videos/demo.m3u) in turn, Camera
uses `FALL_CAMERA` (default `0`; on a Mac with Continuity Camera, `0` may be the iPhone, so try `1`).
From the terminal: `--source 0` (or `FALL_SOURCE=0`). On Apple Silicon set `FALL_DEVICE=mps`. Check a single clip without the service:
`uv run python -m fall_detector --source demo/videos/lie_down.mp4 --show`.

MCP: Streamable HTTP at `http://127.0.0.1:8001/mcp`, or stdio for Claude Desktop — see
[docs/mcp_desktop.md](docs/mcp_desktop.md).

## Layout

```
config/                 settings, symptom catalog, prompts
src/elder_companion/    main web app: chat, voice, symptom log, alerts, companion memory, privacy,
                        agenda, family dashboard, doctor one-pager and memoir
src/fall_detector/      fall-mcp: pose estimation, fall state machine, MCP tools, MJPEG stream
tests/                  unit / integration tests (offline, mock LLM)
eval/                   symptom and memory extraction eval sets
demo/                   demo videos and script
scripts/                fetch_demo_media.py (model + clips)
docs/                   mcp_desktop.md (Claude Desktop setup)
```
