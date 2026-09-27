"""Frame sources: a video file (optionally looped), an .m3u playlist of videos, or a camera index.

Both yield (frame, ts). Files use video time (frame index / fps) so detection is identical at
any processing speed; cameras use monotonic wall time and always hand out the newest frame, so
slow inference drops frames instead of building up lag.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fall_detector.config import PROJECT_ROOT


class SourceError(RuntimeError):
    """The source cannot be opened (missing file, camera unavailable or not permitted)."""


def is_camera(source: str) -> bool:
    return source.strip().isdigit()


def resolve_video(source: str) -> Path:
    p = Path(source)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    if not p.is_file():
        raise SourceError(f"video not found: {source}")
    return p


class VideoFileSource:
    live = False

    def __init__(self, path: str, loop: bool = False, realtime: bool = False):
        import cv2

        self.path = resolve_video(path)
        self.loop = loop
        self.realtime = realtime  # pace playback at the video's own speed (live view of a file)
        self._cap = cv2.VideoCapture(str(self.path))
        if not self._cap.isOpened():
            raise SourceError(f"cannot open video: {path}")
        self.fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.loops = 0  # completed passes; callers reset trackers when it changes
        self._stop = threading.Event()

    def frames(self) -> Iterator[tuple[Any, float]]:
        import cv2

        idx, t0 = 0, time.monotonic()
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok:
                if not self.loop or idx == 0:
                    return
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.loops += 1
                idx, t0 = 0, time.monotonic()
                continue
            ts = idx / self.fps
            idx += 1
            if self.realtime:
                delay = t0 + ts - time.monotonic()
                if delay > 0:
                    self._stop.wait(delay)
            yield frame, ts

    def close(self) -> None:
        self._stop.set()
        self._cap.release()


class CameraSource:
    live = True
    loop = False
    loops = 0

    def __init__(self, index: int, width: int = 1280, height: int = 720):
        import cv2

        self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            raise SourceError(
                f"cannot open camera {index} (on macOS, allow camera access for your terminal in "
                "System Settings > Privacy & Security > Camera)"
            )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.frame_count = 0
        self._latest: tuple[Any, float] | None = None
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read, daemon=True, name="camera-reader")
        self._reader.start()

    def _read(self) -> None:
        misses = 0
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok:
                misses += 1
                if misses > 60:  # ~2s of nothing: camera gone or permission denied
                    break
                time.sleep(0.03)
                continue
            misses = 0
            with self._cond:
                self._latest = (frame, time.monotonic())
                self._cond.notify_all()
        with self._cond:
            self._stop.set()
            self._cond.notify_all()

    def frames(self) -> Iterator[tuple[Any, float]]:
        last_ts = None
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._stop.is_set()
                                    or (self._latest is not None and self._latest[1] != last_ts))
                if self._stop.is_set():
                    return
                frame, last_ts = self._latest  # type: ignore[misc]
            yield frame, last_ts

    def close(self) -> None:
        self._stop.set()
        self._reader.join(timeout=1)
        self._cap.release()


class PlaylistSource:
    """Plays the videos of an .m3u playlist in order (the demo shows every scenario in turn).

    `#EXTINF:-1,Label` names the next entry; paths are relative to the playlist. Each new clip
    bumps `loops`, so callers reset tracking exactly as for a looped video; `clip` is the label
    of the clip playing now.
    """

    live = False

    def __init__(self, path: str, loop: bool = False, realtime: bool = False):
        self.path = resolve_video(path)
        self.entries = parse_playlist(self.path)
        if not self.entries:
            raise SourceError(f"playlist is empty: {path}")
        missing = [str(p.relative_to(PROJECT_ROOT)) if p.is_relative_to(PROJECT_ROOT) else str(p)
                   for p, _ in self.entries if not p.is_file()]
        if missing:
            raise SourceError(f"video not found: {', '.join(missing)} "
                              "(run scripts/fetch_demo_media.py)")
        self.loop = loop
        self.realtime = realtime
        self.loops = 0
        self.index = 0
        self.fps = 30.0
        self.frame_count = 0
        self._current: VideoFileSource | None = None
        self._stop = threading.Event()

    @property
    def clip(self) -> str:
        path, label = self.entries[self.index]
        return f"{self.index + 1}/{len(self.entries)} {label or path.stem}"

    def frames(self) -> Iterator[tuple[Any, float]]:
        first = True
        while not self._stop.is_set():
            for i, (path, _) in enumerate(self.entries):
                if self._stop.is_set():
                    return
                if not first:
                    self.loops += 1  # new clip: time restarts at 0, tracks are new
                first = False
                self.index = i
                self._current = VideoFileSource(str(path), realtime=self.realtime)
                self.fps = self._current.fps
                try:
                    yield from self._current.frames()
                finally:
                    self._current.close()
            if not self.loop:
                return

    def close(self) -> None:
        self._stop.set()
        if self._current is not None:
            self._current.close()


def parse_playlist(path: Path) -> list[tuple[Path, str | None]]:
    entries, label = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            label = line.split(",", 1)[1].strip() if "," in line else None
        elif line and not line.startswith("#"):
            p = Path(line)
            entries.append((p if p.is_absolute() else path.parent / p, label))
            label = None
    return entries


def open_source(source: str, loop: bool = False, realtime: bool = False):
    if is_camera(source):
        return CameraSource(int(source))
    if source.lower().endswith(".m3u"):
        return PlaylistSource(source, loop=loop, realtime=realtime)
    return VideoFileSource(source, loop=loop, realtime=realtime)
