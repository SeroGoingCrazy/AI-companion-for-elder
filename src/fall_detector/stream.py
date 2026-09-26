"""MJPEG stream of the annotated detection view (multipart/x-mixed-replace)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

import anyio

from fall_detector.monitor import MonitorController

BOUNDARY = "frame"
MEDIA_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"
MAX_FPS = 15
KEEPALIVE_S = 1.0  # resend the current frame this often while nothing changes


@lru_cache(maxsize=1)
def idle_frame() -> bytes:
    """Shown while monitoring is off, so the dashboard shows a clear state instead of nothing."""
    import cv2
    import numpy as np

    img = np.full((360, 640, 3), (48, 24, 12), dtype=np.uint8)
    cv2.putText(img, "Fall detection is paused", (150, 175), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (230, 230, 230), 2, cv2.LINE_AA)
    cv2.putText(img, "start_monitoring to resume", (190, 215), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (170, 180, 200), 1, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


def _part(jpeg: bytes) -> bytes:
    return (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
            .encode() + jpeg + b"\r\n")


async def mjpeg_generator(controller: MonitorController, max_fps: float = MAX_FPS) -> AsyncIterator[bytes]:
    last_version, last_sent = -1, 0.0
    while True:
        version, jpeg = controller.latest_frame()
        now = anyio.current_time()
        if jpeg is None or (not controller.status().running and controller.status().frames == 0):
            jpeg = idle_frame()
        if version != last_version or now - last_sent >= KEEPALIVE_S:
            yield _part(jpeg)
            last_version, last_sent = version, now
        await anyio.sleep(1 / max_fps)
