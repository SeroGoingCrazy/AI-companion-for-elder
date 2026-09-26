"""YOLO11-pose wrapper: frame in, tracked people with 17 COCO keypoints out.

The only module that touches ultralytics. Imports are lazy so the pure parts of the package
(rules, events, MCP tools) work without the vision extra.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# COCO keypoint indices
NOSE, L_SHOULDER, R_SHOULDER, L_HIP, R_HIP, L_ANKLE, R_ANKLE = 0, 5, 6, 11, 12, 15, 16
SKELETON = (
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16), (0, 5), (0, 6),
)

Keypoint = tuple[float, float, float]  # x, y, confidence


@dataclass(frozen=True)
class PersonPose:
    track_id: int
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 (pixels)
    keypoints: tuple[Keypoint, ...]  # 17 COCO keypoints
    score: float = 1.0


class PoseEstimator:
    """`YOLO(model).track(frame, persist=True)` with results as PersonPose lists."""

    def __init__(self, model_path: str, device: str = "cpu", conf: float = 0.35, imgsz: int = 640):
        from ultralytics import YOLO

        self._model_path = model_path
        self._device = device
        self._conf = conf
        self._imgsz = imgsz
        self._model = YOLO(model_path)

    def reset(self) -> None:
        """Forget track ids (new source or a looped video)."""
        from ultralytics import YOLO

        self._model = YOLO(self._model_path)

    def track(self, frame: Any) -> list[PersonPose]:
        res = self._model.track(
            frame, persist=True, conf=self._conf, imgsz=self._imgsz, device=self._device,
            classes=[0], verbose=False, tracker="bytetrack.yaml",
        )[0]
        if res.boxes is None or res.keypoints is None or len(res.boxes) == 0:
            return []
        boxes = res.boxes.xyxy.cpu().numpy()
        scores = res.boxes.conf.cpu().numpy()
        ids = res.boxes.id.int().cpu().numpy() if res.boxes.id is not None else None
        kp = res.keypoints.data.cpu().numpy()  # (n, 17, 3)
        people = []
        for i in range(len(boxes)):
            track_id = int(ids[i]) if ids is not None else -(i + 1)  # untracked: negative ids
            people.append(
                PersonPose(
                    track_id=track_id,
                    bbox=tuple(float(v) for v in boxes[i]),
                    keypoints=tuple((float(x), float(y), float(c)) for x, y, c in kp[i]),
                    score=float(scores[i]),
                )
            )
        return people


STATE_COLORS = {  # BGR
    "STANDING": (80, 170, 60),
    "LYING": (200, 150, 40),
    "FALLING": (0, 160, 255),
    "DOWN": (40, 40, 220),
}


def draw(frame: Any, people: list[PersonPose], states: dict[int, str], min_conf: float = 0.3) -> Any:
    """Skeletons, boxes and a state label per person, drawn in place."""
    import cv2

    for p in people:
        state = states.get(p.track_id, "STANDING")
        color = STATE_COLORS.get(state, (200, 200, 200))
        x1, y1, x2, y2 = (int(v) for v in p.bbox)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        for a, b in SKELETON:
            ka, kb = p.keypoints[a], p.keypoints[b]
            if ka[2] >= min_conf and kb[2] >= min_conf:
                cv2.line(frame, (int(ka[0]), int(ka[1])), (int(kb[0]), int(kb[1])), color, 2)
        for x, y, c in p.keypoints:
            if c >= min_conf:
                cv2.circle(frame, (int(x), int(y)), 3, (255, 255, 255), -1)
        label = f"#{p.track_id} {state}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        top = max(y1 - th - 8, 0)
        cv2.rectangle(frame, (x1, top), (x1 + tw + 8, top + th + 8), color, -1)
        cv2.putText(frame, label, (x1 + 4, top + th + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 2, cv2.LINE_AA)
    return frame


def banner(frame: Any, text: str, alarm: bool = False) -> Any:
    """Status strip along the top of the frame."""
    import cv2

    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 30), (40, 40, 220) if alarm else (63, 35, 19), -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
    cv2.putText(frame, text, (10, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return frame
