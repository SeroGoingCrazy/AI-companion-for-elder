"""Fall event store: append-only JSONL + in-memory index + snapshot files (spec 3.6).

fall-mcp is the single source of truth for fall events. Several processes may share the file
(the HTTP server writes, a stdio MCP process for Claude Desktop reads), so reads reload the
index whenever the file has changed on disk.
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

EventSource = Literal["live", "offline"]


@dataclass(frozen=True)
class FallEventRecord:
    event_id: str
    ts: str                      # ISO 8601, UTC
    track_id: int
    confidence: float
    source: EventSource          # live = monitoring (alert pushed); offline = analyze_video
    video: str                   # camera index or video path it came from
    verified: bool | None = None  # optional vision second check (F9); None = not checked
    snapshot_id: str | None = None

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.ts)


def new_event_id(now: datetime) -> str:
    return f"fall_{now.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


class EventStore:
    def __init__(self, events_path: Path, snapshots_dir: Path):
        self.events_path = Path(events_path)
        self.snapshots_dir = Path(snapshots_dir)
        self._lock = threading.Lock()
        self._events: dict[str, FallEventRecord] = {}
        self._loaded_sig: tuple[float, int] | None = None
        self._reload_if_changed()

    def _sig(self) -> tuple[float, int] | None:
        try:
            st = self.events_path.stat()
        except FileNotFoundError:
            return None
        return st.st_mtime, st.st_size

    def _reload_if_changed(self) -> None:
        sig = self._sig()
        if sig == self._loaded_sig:
            return
        events: dict[str, FallEventRecord] = {}
        if sig is not None:
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = FallEventRecord(**json.loads(line))
                except (ValueError, TypeError):
                    continue  # a torn or foreign line must not take the whole store down
                events[rec.event_id] = rec
        self._events, self._loaded_sig = events, sig

    def add(
        self,
        *,
        track_id: int,
        confidence: float,
        source: EventSource,
        video: str,
        snapshot_jpeg: bytes | None = None,
        now: datetime | None = None,
    ) -> FallEventRecord:
        now = now or datetime.now(UTC)
        event_id = new_event_id(now)
        snapshot_id = None
        if snapshot_jpeg:
            self.snapshots_dir.mkdir(parents=True, exist_ok=True)
            (self.snapshots_dir / f"{event_id}.jpg").write_bytes(snapshot_jpeg)
            snapshot_id = event_id
        rec = FallEventRecord(
            event_id=event_id, ts=now.isoformat(timespec="seconds"), track_id=track_id,
            confidence=confidence, source=source, video=video, snapshot_id=snapshot_id,
        )
        with self._lock:
            self._reload_if_changed()
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec)) + "\n")
            self._events[rec.event_id] = rec
            self._loaded_sig = self._sig()
        return rec

    def get(self, event_id: str) -> FallEventRecord | None:
        with self._lock:
            self._reload_if_changed()
            return self._events.get(event_id)

    def query(
        self, since: datetime | None = None, limit: int = 20, source: EventSource | None = None
    ) -> list[FallEventRecord]:
        """Newest first."""
        with self._lock:
            self._reload_if_changed()
            events = list(self._events.values())
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=UTC)
            events = [e for e in events if e.when >= since]
        if source is not None:
            events = [e for e in events if e.source == source]
        events.sort(key=lambda e: e.ts, reverse=True)
        return events[:limit]

    def snapshot_path(self, event_id: str) -> Path | None:
        rec = self.get(event_id)
        if rec is None or rec.snapshot_id is None:
            return None
        p = self.snapshots_dir / f"{rec.snapshot_id}.jpg"
        return p if p.is_file() else None
