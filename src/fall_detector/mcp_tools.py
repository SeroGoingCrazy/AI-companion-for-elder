"""fall-mcp tools (spec 3.6). Only talks to MonitorController and EventStore, never to YOLO."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

import anyio
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from fall_detector.events import EventStore
from fall_detector.monitor import MonitorController
from fall_detector.sources import SourceError

INSTRUCTIONS = (
    "Fall detection for an older adult living alone. A camera (or demo video) is watched by a "
    "pose model; a fall is someone dropping to the floor fast and staying down for a few "
    "seconds. Use get_fall_events to answer 'did she fall?', get_event_snapshot to look at "
    "what the camera saw, and get_monitor_status for what is happening right now. Times are UTC."
)


def _parse_since(since: str | None) -> datetime | None:
    if not since:
        return None
    try:
        return datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as e:
        raise ToolError(f"since must be an ISO 8601 time like 2026-09-26T00:00:00Z, got {since!r}") from e


def build_mcp(controller: MonitorController, store: EventStore) -> MCPServer:
    mcp = MCPServer("fall-mcp", instructions=INSTRUCTIONS)

    @mcp.tool()
    def get_fall_events(since: str | None = None, limit: int = 20,
                        include_offline: bool = False) -> list[dict[str, Any]]:
        """List detected falls, newest first.

        Args:
            since: only events at or after this ISO 8601 time (UTC if no offset), e.g. "2026-09-26T00:00:00Z".
            limit: maximum number of events (1-100).
            include_offline: also include events found by analyze_video (not real incidents).
        """
        limit = max(1, min(limit, 100))
        events = store.query(since=_parse_since(since), limit=limit,
                             source=None if include_offline else "live")
        return [asdict(e) for e in events]

    @mcp.tool(structured_output=False)  # text + image content blocks
    def get_event_snapshot(event_id: str) -> list[Any]:
        """The camera frame (with the detected skeleton) saved when a fall was confirmed.

        Args:
            event_id: an event_id from get_fall_events.
        """
        rec = store.get(event_id)
        if rec is None:
            raise ToolError(f"no fall event {event_id!r}; call get_fall_events for valid ids")
        path = store.snapshot_path(event_id)
        if path is None:
            raise ToolError(f"event {event_id} has no snapshot")
        return [
            f"Fall event {rec.event_id} at {rec.ts} (track {rec.track_id}, confidence "
            f"{rec.confidence:.2f}, source {rec.source}). The person is highlighted with a box "
            "and skeleton; red means DOWN (on the floor).",
            Image(data=path.read_bytes(), format="jpeg"),
        ]

    @mcp.tool()
    def get_monitor_status() -> dict[str, Any]:
        """Whether monitoring is running, on which source, and each person's current state
        (STANDING / LYING / FALLING / DOWN)."""
        return controller.status().to_dict()

    @mcp.tool()
    def start_monitoring(source: str = "0", loop: bool = True) -> dict[str, Any]:
        """Start (or switch) live fall monitoring. Falls found while monitoring alert the family
        dashboard.

        Args:
            source: camera index such as "0", or a video file path such as "demo/videos/fall_01.mp4".
            loop: replay a video file when it ends (ignored for cameras).
        """
        try:
            s = controller.start(source, loop=loop)
        except SourceError as e:
            raise ToolError(str(e)) from e
        return {"running": s.running, "source": s.source, "loop": s.loop}

    @mcp.tool()
    def stop_monitoring() -> dict[str, Any]:
        """Stop live fall monitoring."""
        s = controller.stop()
        return {"running": s.running}

    @mcp.tool()
    async def analyze_video(path: str) -> dict[str, Any]:
        """Check a recorded video for falls (e.g. a clip the family uploaded). Runs offline:
        does not alert the dashboard; events are stored with source "offline".

        Args:
            path: video file path, absolute or relative to the project, e.g. "demo/videos/lie_down.mp4".
        """
        try:
            return await anyio.to_thread.run_sync(controller.analyze_video, path)
        except SourceError as e:
            raise ToolError(str(e)) from e

    @mcp.resource("fall://live/snapshot", mime_type="image/jpeg")
    def live_snapshot() -> bytes:
        """The current annotated frame of the live view."""
        _, jpeg = controller.latest_frame()
        if jpeg is None:
            raise ToolError("monitoring has not produced a frame yet")
        return jpeg

    return mcp
