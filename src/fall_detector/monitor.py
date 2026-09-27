"""MonitorController: owns the background detection thread (spec F6).

One live source at a time: start() stops the current one first. The thread runs
source -> pose -> rules -> annotated JPEG; fall events go to the EventStore and, for live
monitoring, to the Reporter. MCP tools and HTTP routes only talk to this class.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fall_detector.config import FallConfig
from fall_detector.events import EventStore, FallEventRecord
from fall_detector.reporter import Reporter
from fall_detector.rules import DOWN, FALLING, FallDetector
from fall_detector.sources import SourceError, is_camera, open_source

log = logging.getLogger(__name__)

JPEG_QUALITY = 80


@dataclass
class MonitorStatus:
    running: bool = False
    source: str | None = None
    loop: bool = False
    live: bool = False               # camera
    clip: str | None = None          # playlist: the scenario playing now, e.g. "2/6 Fall"
    fps: float = 0.0
    persons: int = 0
    states: dict[int, str] = field(default_factory=dict)
    uptime_s: float = 0.0
    frames: int = 0
    events: int = 0                  # fall events raised since start
    ended: bool = False              # a non-looping video reached its end
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["states"] = {str(k): v for k, v in self.states.items()}
        return d


def _encode_jpeg(frame: Any) -> bytes:
    import cv2

    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return buf.tobytes() if ok else b""


def _default_estimator(cfg: FallConfig):
    from fall_detector.pose import PoseEstimator

    return PoseEstimator(cfg.model_path, device=cfg.device)


class MonitorController:
    def __init__(
        self,
        cfg: FallConfig,
        store: EventStore,
        reporter: Reporter | None = None,
        estimator_factory: Callable[[FallConfig], Any] = _default_estimator,
        source_factory: Callable[..., Any] = open_source,
    ):
        self.cfg = cfg
        self.store = store
        self.reporter = reporter
        self._estimator_factory = estimator_factory
        self._source_factory = source_factory
        self._live_estimator: Any = None
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._src: Any = None
        self._stop = threading.Event()
        self._status = MonitorStatus()
        self._started_at = 0.0
        self._frame: bytes | None = None
        self._frame_version = 0

    # ---------- control ----------

    def start(self, source: str, loop: bool = True) -> MonitorStatus:
        """Start monitoring `source` (camera index like "0", or a video path). Raises SourceError."""
        source = str(source).strip()
        src = self._source_factory(source, loop=loop, realtime=True)  # a bad source keeps the old one
        self.stop()
        with self._lock:
            if self._live_estimator is None:
                self._live_estimator = self._estimator_factory(self.cfg)
            self._src = src
            self._stop = threading.Event()
            self._started_at = time.monotonic()
            self._status = MonitorStatus(running=True, source=source, loop=loop and not src.live,
                                         live=src.live)
            self._thread = threading.Thread(target=self._run, args=(src, self._stop), daemon=True,
                                            name="fall-monitor")
            self._thread.start()
        log.info("monitoring %s (loop=%s)", source, loop)
        return self.status()

    def stop(self) -> MonitorStatus:
        with self._lock:
            thread, src = self._thread, self._src
            self._stop.set()
        if src is not None:
            src.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        with self._lock:
            self._thread = self._src = None
            self._status.running = False
        return self.status()

    def close(self) -> None:
        self.stop()
        if self.reporter:
            self.reporter.close()

    def status(self) -> MonitorStatus:
        with self._lock:
            s = MonitorStatus(**{**asdict(self._status), "states": dict(self._status.states)})
        if s.running:
            s.uptime_s = round(time.monotonic() - self._started_at, 1)
        return s

    def latest_frame(self) -> tuple[int, bytes | None]:
        """(version, JPEG) of the newest annotated frame; version changes with every frame."""
        with self._lock:
            return self._frame_version, self._frame

    # ---------- detection loop ----------

    def _run(self, src: Any, stop: threading.Event) -> None:
        from fall_detector.pose import banner, draw

        est = self._live_estimator
        est.reset()
        det = FallDetector(self.cfg.rules)
        loops, t_prev, fps = 0, time.perf_counter(), 0.0
        try:
            for frame, ts in src.frames():
                if stop.is_set():
                    break
                if src.loops != loops:  # looped video: time restarts, tracks are new
                    loops = src.loops
                    est.reset()
                    det.reset()
                people = est.track(frame)
                events = det.update(people, ts)
                states = det.states
                draw(frame, people, states, self.cfg.rules.min_keypoint_conf)
                now = time.perf_counter()
                fps = 0.9 * fps + 0.1 * (1 / max(now - t_prev, 1e-6)) if fps else 1 / max(now - t_prev, 1e-6)
                t_prev = now
                alarm = any(s in (DOWN, FALLING) for s in states.values())
                label = "FALL DETECTED" if DOWN in states.values() else (
                    "Possible fall..." if FALLING in states.values() else "Monitoring")
                clip = getattr(src, "clip", None)
                banner(frame, f"{label}   {clip + '   ' if clip else ''}{len(people)} person(s)   "
                              f"{fps:4.1f} FPS", alarm=alarm)
                jpeg = _encode_jpeg(frame)
                with self._lock:
                    if stop.is_set():
                        break
                    self._frame, self._frame_version = jpeg, self._frame_version + 1
                    st = self._status
                    st.fps, st.persons, st.states = round(fps, 1), len(people), states
                    st.clip = clip
                    st.frames += 1
                    st.events += len(events)
                for ev in events:
                    rec = self.store.add(track_id=ev.track_id, confidence=ev.confidence,
                                         source="live", video=self._status.source or "",
                                         snapshot_jpeg=jpeg)
                    log.warning("FALL %s (track %d)", rec.event_id, ev.track_id)
                    if self.reporter:
                        self.reporter.report(rec, down_for_s=ev.ts - ev.fall_ts)
            else:
                with self._lock:
                    if not stop.is_set():
                        self._status.ended = True
        except Exception as e:  # never let the thread die silently
            log.exception("monitor crashed")
            with self._lock:
                self._status.error = str(e)
        finally:
            with self._lock:
                if self._src is src:
                    self._status.running = False

    # ---------- offline ----------

    def analyze_video(self, path: str) -> dict[str, Any]:
        """Run the whole video through detection as fast as possible (no pacing, no alerts).
        Events are stored with source=offline. Uses its own model instance."""
        if is_camera(path):
            raise SourceError("analyze_video needs a video file, not a camera")
        src = self._source_factory(path, loop=False, realtime=False)  # SourceError if missing
        est = self._estimator_factory(self.cfg)
        det = FallDetector(self.cfg.rules)
        found: list[FallEventRecord] = []
        frames, t0, last_ts, jpeg_frame = 0, time.perf_counter(), 0.0, None
        try:
            from fall_detector.pose import draw

            for frame, ts in src.frames():
                people = est.track(frame)
                events = det.update(people, ts)
                frames, last_ts = frames + 1, ts
                for ev in events:
                    jpeg_frame = _encode_jpeg(draw(frame, people, det.states,
                                                   self.cfg.rules.min_keypoint_conf))
                    rec = self.store.add(track_id=ev.track_id, confidence=ev.confidence,
                                         source="offline", video=str(Path(path).as_posix()),
                                         snapshot_jpeg=jpeg_frame)
                    found.append(rec)
        finally:
            src.close()
        return {
            "path": path,
            "frames": frames,
            "duration_s": round(last_ts, 2),
            "processing_s": round(time.perf_counter() - t0, 2),
            "events": [asdict(r) for r in found],
        }
