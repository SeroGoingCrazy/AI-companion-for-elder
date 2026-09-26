"""Fall rules (spec 3.5): keypoints -> features -> per-person state machine -> fall events.

Pure Python: no model, no OpenCV, no I/O. Time comes in as `ts` seconds (video time for files,
monotonic wall time for cameras), so everything here is testable with synthetic sequences.

    STANDING --horizontal + fast hip drop--> FALLING --horizontal and still >= down_confirm_s--> DOWN (event)
    STANDING --horizontal, slow-----------> LYING --fast hip drop--> FALLING
                                            (lying down slowly on purpose never alerts)
    any      --upright for UPRIGHT_S-------> STANDING
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from fall_detector.config import RuleConfig
from fall_detector.pose import L_HIP, L_SHOULDER, R_HIP, R_SHOULDER, PersonPose

STANDING, LYING, FALLING, DOWN = "STANDING", "LYING", "FALLING", "DOWN"

UPRIGHT_S = 0.5        # upright this long to leave FALLING / DOWN (ignores one-frame flickers)
LOOKBACK_S = 1.0       # the drop may finish shortly before the body reads as horizontal
REF_HEIGHT_S = 3.0     # standing height = tallest upright bbox within this window
STILL_RATIO = 0.25     # "still" = hip moved less than this x standing height over STILL_S
STILL_S = 1.0
LOST_TRACK_S = 5.0     # forget a person unseen this long
ADOPT_S = 1.5          # a new track id this soon after one vanished nearby is the same person


@dataclass(frozen=True)
class Features:
    ts: float
    aspect_ratio: float          # bbox width / height
    torso_angle: float | None    # degrees from vertical (shoulder mid -> hip mid); None if unsure
    hip_y: float                 # hip midpoint y in pixels (bbox centre if hips are not visible)
    height: float                # bbox height in pixels
    confidence: float            # detection score


@dataclass(frozen=True)
class FallEvent:
    track_id: int
    ts: float                    # when DOWN was confirmed
    fall_ts: float               # when the fall started
    confidence: float


def _mid(a: tuple[float, float, float], b: tuple[float, float, float], min_conf: float):
    ok = [p for p in (a, b) if p[2] >= min_conf]
    if not ok:
        return None
    return sum(p[0] for p in ok) / len(ok), sum(p[1] for p in ok) / len(ok)


def compute_features(pose: PersonPose, ts: float, min_conf: float = 0.3) -> Features:
    x1, y1, x2, y2 = pose.bbox
    w, h = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
    kp = pose.keypoints
    shoulder = _mid(kp[L_SHOULDER], kp[R_SHOULDER], min_conf)
    hip = _mid(kp[L_HIP], kp[R_HIP], min_conf)
    angle = None
    if shoulder and hip:
        dx, dy = hip[0] - shoulder[0], hip[1] - shoulder[1]
        if math.hypot(dx, dy) >= 0.1 * h:  # too short a torso gives a meaningless angle
            angle = math.degrees(math.atan2(abs(dx), abs(dy)))
    hip_y = hip[1] if hip else (y1 + y2) / 2
    return Features(ts=ts, aspect_ratio=w / h, torso_angle=angle, hip_y=hip_y, height=h,
                    confidence=pose.score)


def is_horizontal(f: Features, cfg: RuleConfig) -> bool:
    """Lying posture: a wide bbox, or a near-horizontal torso in a bbox that is not tall and thin
    (bending over to pick something up tilts the torso but keeps the bbox upright)."""
    if f.aspect_ratio > cfg.aspect_ratio_threshold:
        return True
    return f.torso_angle is not None and f.torso_angle > cfg.torso_angle_deg and f.aspect_ratio > 0.8


@dataclass
class _Sample:
    ts: float
    hip_y: float
    height: float
    upright: bool


@dataclass
class FallStateMachine:
    """One person. Feed `update()` every frame the person is seen."""

    cfg: RuleConfig
    state: str = STANDING
    ref_height: float | None = None
    fall_ts: float | None = None
    upright_since: float | None = None
    last_alert_ts: float = -math.inf
    last_seen: float = -math.inf
    last_center: tuple[float, float] = (0.0, 0.0)
    _hist: deque[_Sample] = field(default_factory=deque)

    def update(self, f: Features, center: tuple[float, float] = (0.0, 0.0)) -> FallEvent | None:
        horizontal = is_horizontal(f, self.cfg)
        self.last_seen, self.last_center = f.ts, center
        self._hist.append(_Sample(f.ts, f.hip_y, f.height, not horizontal))
        keep = max(REF_HEIGHT_S, self.cfg.drop_window_s + LOOKBACK_S, STILL_S)
        while self._hist and f.ts - self._hist[0].ts > keep:
            self._hist.popleft()
        upright_heights = [s.height for s in self._hist if s.upright and f.ts - s.ts <= REF_HEIGHT_S]
        if upright_heights:
            self.ref_height = max(upright_heights)
        ref = self.ref_height or f.height

        if horizontal:
            self.upright_since = None
        elif self.upright_since is None:
            self.upright_since = f.ts
        upright_long = self.upright_since is not None and f.ts - self.upright_since >= UPRIGHT_S

        if self.state in (STANDING, LYING):
            if not horizontal:
                self.state = STANDING
            elif self._fast_drop(f.ts, ref):
                # also from LYING: a fall that pitches forward reads horizontal before the hips drop
                self.state, self.fall_ts = FALLING, f.ts
            else:
                self.state = LYING
        elif self.state == FALLING:
            if upright_long:
                self.state, self.fall_ts = STANDING, None
            elif horizontal and f.ts - (self.fall_ts or f.ts) >= self.cfg.down_confirm_s \
                    and self._still(f.ts, ref):
                self.state = DOWN
                if f.ts - self.last_alert_ts >= self.cfg.cooldown_s:
                    self.last_alert_ts = f.ts
                    return FallEvent(track_id=-1, ts=f.ts, fall_ts=self.fall_ts or f.ts,
                                     confidence=round(f.confidence, 3))
        elif self.state == DOWN and upright_long:
            self.state, self.fall_ts = STANDING, None
        return None

    def _fast_drop(self, now: float, ref: float) -> bool:
        """Did the hips drop by drop_ratio x standing height within drop_window_s, recently?"""
        pts = [s for s in self._hist if now - s.ts <= self.cfg.drop_window_s + LOOKBACK_S]
        need = self.cfg.drop_ratio * ref
        lo = 0
        for j, later in enumerate(pts):  # two pointers over time-ordered samples
            while later.ts - pts[lo].ts > self.cfg.drop_window_s:
                lo += 1
            if later.hip_y - min(s.hip_y for s in pts[lo:j + 1]) >= need:
                return True
        return False

    def _still(self, now: float, ref: float) -> bool:
        ys = [s.hip_y for s in self._hist if now - s.ts <= STILL_S]
        return not ys or max(ys) - min(ys) < STILL_RATIO * ref


class FallDetector:
    """All people in a scene: routes poses to state machines by track id."""

    def __init__(self, cfg: RuleConfig):
        self.cfg = cfg
        self._machines: dict[int, FallStateMachine] = {}

    def reset(self) -> None:
        self._machines.clear()

    @property
    def states(self) -> dict[int, str]:
        return {tid: m.state for tid, m in self._machines.items()}

    def update(self, people: list[PersonPose], ts: float) -> list[FallEvent]:
        events: list[FallEvent] = []
        people = [p for p in people if p.track_id >= 0]  # untracked one-off boxes are noise
        seen = {p.track_id for p in people}
        for p in people:
            f = compute_features(p, ts, self.cfg.min_keypoint_conf)
            x1, y1, x2, y2 = p.bbox
            center = ((x1 + x2) / 2, (y1 + y2) / 2)
            m = self._machines.get(p.track_id) or self._adopt(p.track_id, center, ts, seen)
            ev = m.update(f, center)
            if ev:
                events.append(FallEvent(p.track_id, ev.ts, ev.fall_ts, ev.confidence))
        for tid in [t for t, m in self._machines.items() if ts - m.last_seen > LOST_TRACK_S]:
            del self._machines[tid]
        return events

    def _adopt(self, tid: int, center: tuple[float, float], ts: float, seen: set[int]) -> FallStateMachine:
        """A falling body often gets a new track id; hand it the history of the person who just
        vanished at the same spot, so the fall is not lost."""
        best, best_d = None, math.inf
        for old_id, m in self._machines.items():
            if old_id in seen or ts - m.last_seen > ADOPT_S:
                continue
            d = math.dist(center, m.last_center)
            if d < (m.ref_height or 0) and d < best_d:
                best, best_d = old_id, d
        machine = self._machines.pop(best) if best is not None else FallStateMachine(self.cfg)
        self._machines[tid] = machine
        return machine
