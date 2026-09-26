"""Fakes for fall_detector tests: scripted poses instead of YOLO, synthetic frames instead of video.

A FakeSource plays a named scenario ("fake:fall", "fake:walk", "fake:lie_down"); each frame
carries its index in two pixels, and FakeEstimator turns that index back into the scripted pose.
"""

from __future__ import annotations

import math

from fall_detector.pose import L_HIP, L_SHOULDER, R_HIP, R_SHOULDER, PersonPose
from fall_detector.sources import SourceError

FPS = 30
UPRIGHT = dict(w=100.0, h=300.0, angle=0.0)
FLAT = dict(w=300.0, h=100.0, angle=85.0)

SCENARIOS: dict[str, list[tuple[float, dict, dict]]] = {
    "fall": [(1, UPRIGHT, UPRIGHT), (0.4, UPRIGHT, FLAT), (4, FLAT, FLAT)],
    "lie_down": [(1, UPRIGHT, UPRIGHT), (3, UPRIGHT, FLAT), (4, FLAT, FLAT)],
    "walk": [(3, UPRIGHT, UPRIGHT)],
}


def person(w: float, h: float, angle: float, track_id: int = 1, cx: float = 320.0,
           bottom: float = 400.0, conf: float = 0.9) -> PersonPose:
    hip = (cx, bottom - h / 2)
    torso = 0.3 * max(w, h)
    a = math.radians(angle)
    shoulder = (hip[0] - torso * math.sin(a), hip[1] - torso * math.cos(a))
    kps = [(cx, bottom - h / 2, conf)] * 17
    kps[L_SHOULDER] = kps[R_SHOULDER] = (*shoulder, conf)
    kps[L_HIP] = kps[R_HIP] = (*hip, conf)
    return PersonPose(track_id, (cx - w / 2, bottom - h, cx + w / 2, bottom), tuple(kps), 0.9)


def blend(a: dict, b: dict, k: float) -> dict:
    return {key: a[key] + (b[key] - a[key]) * k for key in a}


def scenario_poses(name: str) -> list[PersonPose]:
    poses = []
    for duration, a, b in SCENARIOS[name]:
        n = max(int(duration * FPS), 1)
        poses += [person(**blend(a, b, i / n)) for i in range(n)]
    return poses


class FakeSource:
    live = False

    def __init__(self, source: str, loop: bool = False, realtime: bool = False):
        if not source.startswith("fake:") or source[5:] not in SCENARIOS:
            raise SourceError(f"video not found: {source}")
        self.name = source[5:]
        self.loop = loop
        self.loops = 0
        self.fps = FPS
        self.frame_count = len(scenario_poses(self.name))
        self._closed = False

    def frames(self):
        import numpy as np

        while not self._closed:
            for i in range(self.frame_count):
                if self._closed:
                    return
                frame = np.zeros((240, 320, 3), dtype=np.uint8)
                frame[0, 0, 0], frame[0, 0, 1] = i % 256, i // 256
                frame[0, 1, 0] = ord(self.name[0])
                yield frame, i / FPS
            if not self.loop:
                return
            self.loops += 1

    def close(self) -> None:
        self._closed = True


class FakeEstimator:
    def __init__(self, cfg=None):
        self._cache: dict[str, list[PersonPose]] = {}
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1

    def track(self, frame) -> list[PersonPose]:
        idx = int(frame[0, 0, 0]) + 256 * int(frame[0, 0, 1])
        name = next(n for n in SCENARIOS if ord(n[0]) == int(frame[0, 1, 0]))
        poses = self._cache.setdefault(name, scenario_poses(name))
        return [poses[idx]]
