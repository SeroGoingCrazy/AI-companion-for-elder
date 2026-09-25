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

Fall detection needs the optional vision dependencies:

```bash
uv sync --extra vision
```

## Layout

```
config/                 settings, symptom catalog, prompts
src/elder_companion/    main web app: chat, voice, symptom log, alerts, family dashboard
src/fall_detector/      fall-mcp: pose estimation, fall state machine, MCP tools, MJPEG stream
tests/                  unit / integration tests (offline, mock LLM)
eval/                   symptom extraction eval set
demo/                   demo videos and script
```
