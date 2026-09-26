"""F3/F4: features and the fall state machine, on synthetic keypoint sequences."""

from __future__ import annotations

import math

import pytest

from fall_detector.config import RuleConfig
from fall_detector.pose import L_HIP, L_SHOULDER, R_HIP, R_SHOULDER, PersonPose
from fall_detector.rules import (
    DOWN,
    FALLING,
    LYING,
    STANDING,
    FallDetector,
    compute_features,
    is_horizontal,
)

pytestmark = pytest.mark.unit

FPS = 30
CFG = RuleConfig(
    min_keypoint_conf=0.3, aspect_ratio_threshold=1.2, torso_angle_deg=60,
    drop_ratio=0.25, drop_window_s=0.6, down_confirm_s=3, cooldown_s=30,
)
UPRIGHT = dict(w=100.0, h=300.0, angle=0.0)
FLAT = dict(w=300.0, h=100.0, angle=85.0)


def person(w: float, h: float, angle: float, track_id: int = 1, cx: float = 320.0,
           bottom: float = 400.0, conf: float = 0.9) -> PersonPose:
    """A body whose hips sit mid-bbox and whose torso is tilted `angle` degrees from vertical."""
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


def run(segments: list[tuple[float, dict, dict]], ids=lambda t: 1) -> tuple[list, list[str]]:
    """segments: (duration_s, from_pose, to_pose). Returns fall events and the state per frame."""
    det, events, states, t = FallDetector(CFG), [], [], 0.0
    for duration, a, b in segments:
        n = max(int(duration * FPS), 1)
        for i in range(n):
            tid = ids(t)
            events += det.update([person(**blend(a, b, i / n), track_id=tid)], t)
            states.append(det.states.get(tid))
            t += 1 / FPS
    return events, states


def test_upright_features() -> None:
    f = compute_features(person(**UPRIGHT), 0.0)
    assert f.aspect_ratio == pytest.approx(1 / 3)
    assert f.torso_angle == pytest.approx(0, abs=0.1)
    assert f.hip_y == pytest.approx(250)
    assert not is_horizontal(f, CFG)


def test_horizontal_features() -> None:
    f = compute_features(person(**FLAT), 0.0)
    assert f.aspect_ratio == pytest.approx(3)
    assert f.torso_angle == pytest.approx(85, abs=0.1)
    assert is_horizontal(f, CFG)


def test_bending_over_is_not_horizontal() -> None:
    # torso near horizontal but the body box is still tall: picking something up
    f = compute_features(person(w=150, h=280, angle=80), 0.0)
    assert not is_horizontal(f, CFG)


def test_low_confidence_keypoints_fall_back_to_bbox() -> None:
    f = compute_features(person(**UPRIGHT, conf=0.1), 0.0)
    assert f.torso_angle is None
    assert f.hip_y == pytest.approx(250)  # bbox centre


def test_fast_fall_then_down_alerts_once() -> None:
    events, states = run([(1, UPRIGHT, UPRIGHT), (0.4, UPRIGHT, FLAT), (4, FLAT, FLAT)])
    assert len(events) == 1
    assert FALLING in states and states[-1] == DOWN
    ev = events[0]
    assert ev.track_id == 1
    assert ev.ts - ev.fall_ts >= CFG.down_confirm_s


def test_slowly_lying_down_never_alerts() -> None:
    events, states = run([(1, UPRIGHT, UPRIGHT), (3, UPRIGHT, FLAT), (10, FLAT, FLAT)])
    assert events == []
    assert FALLING not in states and states[-1] == LYING


def test_getting_up_within_confirm_window_does_not_alert() -> None:
    events, states = run([(1, UPRIGHT, UPRIGHT), (0.4, UPRIGHT, FLAT), (1, FLAT, FLAT),
                          (0.3, FLAT, UPRIGHT), (2, UPRIGHT, UPRIGHT)])
    assert events == []
    assert FALLING in states and states[-1] == STANDING


def test_staying_down_for_a_minute_alerts_once() -> None:
    events, _ = run([(1, UPRIGHT, UPRIGHT), (0.4, UPRIGHT, FLAT), (60, FLAT, FLAT)])
    assert len(events) == 1


def test_second_fall_within_cooldown_is_silent_and_after_it_alerts() -> None:
    fall = [(0.4, UPRIGHT, FLAT), (4, FLAT, FLAT), (0.3, FLAT, UPRIGHT), (2, UPRIGHT, UPRIGHT)]
    events, _ = run([(1, UPRIGHT, UPRIGHT)] + fall + fall)
    assert len(events) == 1  # second fall ~7s later, inside the 30s cooldown
    events, _ = run([(1, UPRIGHT, UPRIGHT)] + fall + [(30, UPRIGHT, UPRIGHT)] + fall)
    assert len(events) == 2


def test_forward_pitch_then_drop_still_counts_as_a_fall() -> None:
    # tips forward first (horizontal while the hips are still high), then drops fast
    pitched = dict(w=250.0, h=200.0, angle=80.0)
    events, states = run([(1, UPRIGHT, UPRIGHT), (0.5, UPRIGHT, pitched),
                          (0.3, dict(pitched, h=300.0), FLAT), (4, FLAT, FLAT)])
    assert LYING in states and FALLING in states
    assert len(events) == 1


def test_new_track_id_mid_fall_keeps_the_fall() -> None:
    # the tracker hands the body a new id as it hits the floor
    events, _ = run([(1, UPRIGHT, UPRIGHT), (0.4, UPRIGHT, FLAT), (4, FLAT, FLAT)],
                    ids=lambda t: 1 if t < 1.3 else 7)
    assert [e.track_id for e in events] == [7]


def test_untracked_detections_are_ignored() -> None:
    det = FallDetector(CFG)
    assert det.update([person(**FLAT, track_id=-1)], 0.0) == []
    assert det.states == {}


def test_lost_track_is_forgotten() -> None:
    det = FallDetector(CFG)
    det.update([person(**UPRIGHT)], 0.0)
    det.update([], 10.0)
    assert det.states == {}
