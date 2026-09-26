# Connecting fall-mcp to Claude Desktop

fall-mcp exposes fall detection as MCP tools, so any MCP client can ask "did Mom fall today?"
and look at the snapshot. Two ways to connect:

| Transport | Command | Use it for |
|---|---|---|
| stdio | `python -m fall_detector.server --stdio` | Claude Desktop (it starts the process itself) |
| Streamable HTTP | `http://127.0.0.1:8001/mcp` (started by `python -m fall_detector.server`) | Claude Code, MCP Inspector, the dashboard agent (E5) |

Both read the same event store (`data/fall_events.jsonl` + `data/snapshots/`), so events found by
the running service are visible to the stdio process Claude Desktop starts.

## Prerequisites

```
uv sync --extra vision
uv run python scripts/fetch_demo_media.py     # pose model + demo clips
```

## Claude Desktop (stdio)

Open Claude Desktop → Settings → Developer → Edit Config, which opens `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add the server. Point `command` at the project's virtualenv Python (the most reliable option: no
PATH lookup, no dependency sync on launch). Paths must be absolute.

**macOS**

```json
{
  "mcpServers": {
    "fall-mcp": {
      "command": "/Users/you/AI-for-elder/.venv/bin/python",
      "args": ["-m", "fall_detector.server", "--stdio"]
    }
  }
}
```

**Windows**

```json
{
  "mcpServers": {
    "fall-mcp": {
      "command": "C:\\Users\\you\\AI-for-elder\\.venv\\Scripts\\python.exe",
      "args": ["-m", "fall_detector.server", "--stdio"]
    }
  }
}
```

Alternative with uv (uv must be on PATH, or use its full path):
`"command": "uv", "args": ["run", "--directory", "/abs/path/AI-for-elder", "--extra", "vision", "python", "-m", "fall_detector.server", "--stdio"]`.

Restart Claude Desktop. The tools menu should list **fall-mcp** with 6 tools.

## Try it

With the service running and the demo fall played (see the README), ask Claude Desktop:

> Were any falls detected today? Show me the snapshot.

Expected: Claude calls `get_fall_events`, then `get_event_snapshot`, and shows the frame with the
person marked DOWN. Other things to ask:

- "What is the fall camera seeing right now?" → `get_monitor_status`
- "Check demo/videos/lie_down.mp4 for falls." → `analyze_video` (0 events: lying down slowly is not a fall)
- "Start monitoring camera 0." → `start_monitoring` (on macOS, allow camera access for Claude first)

## Tools

| Tool | What it does |
|---|---|
| `get_fall_events(since?, limit=20, include_offline=false)` | Falls newest first (ISO 8601 `since`, UTC) |
| `get_event_snapshot(event_id)` | JPEG of the moment the fall was confirmed + a text description |
| `get_monitor_status()` | Running?, source, FPS, each person's state (STANDING / LYING / FALLING / DOWN) |
| `start_monitoring(source="0", loop=true)` | Camera index or video path; falls alert the dashboard |
| `stop_monitoring()` | Stop the detection thread |
| `analyze_video(path)` | Offline check of a recording; no alerts, events stored as `offline` |

Resource: `fall://live/snapshot` (current annotated frame).

## Claude Code / MCP Inspector (HTTP)

Start the service (`uv run python -m fall_detector.server`), then:

```
claude mcp add --transport http fall-mcp http://127.0.0.1:8001/mcp
npx @modelcontextprotocol/inspector      # transport: Streamable HTTP, URL http://127.0.0.1:8001/mcp
```

## Troubleshooting

- **Server disconnected right away**: run the exact `command` + `args` in a terminal. It should
  wait silently for input (stdout carries MCP traffic, logs go to stderr). Missing packages →
  `uv sync --extra vision`.
- **No events**: events come from the HTTP service or `analyze_video`; play the demo fall first
  (`uv run python -m fall_detector.ctl play demo/videos/fall_01.mp4`).
- **Camera fails on macOS**: System Settings → Privacy & Security → Camera → allow the app that
  launched the server (Terminal, iTerm, or Claude).
