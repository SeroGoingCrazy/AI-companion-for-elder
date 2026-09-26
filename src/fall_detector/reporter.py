"""Push live fall events to the main service: POST /api/alerts (the frozen contract, spec 5.5).

Reports go through a background worker so the detection loop never waits on the network; a
failed report is retried and then logged, never raised.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

import httpx

from fall_detector.events import FallEventRecord

log = logging.getLogger(__name__)

RETRIES = 3
BACKOFF_S = (1.0, 2.0, 4.0)


def alert_payload(rec: FallEventRecord, down_for_s: float | None = None) -> dict:
    where = "the camera" if rec.video.strip().isdigit() else "the monitored room"
    lying = f" for {down_for_s:.0f}s" if down_for_s else ""
    return {
        "type": "fall",
        "level": "high",
        "title": "Fall detected",
        "content": f"Someone fell and has stayed on the floor{lying} in view of {where}. "
                   "Please check on them.",
        # the main service serves data/snapshots/x.jpg at /media/snapshots/x.jpg
        "snapshot_path": f"snapshots/{rec.snapshot_id}.jpg" if rec.snapshot_id else None,
        "ref_id": rec.event_id,
    }


class Reporter:
    def __init__(self, url: str, client: httpx.Client | None = None, backoff: tuple[float, ...] = BACKOFF_S):
        self.url = url
        self._client = client or httpx.Client(timeout=5)
        self._backoff = backoff
        self._queue: queue.Queue[tuple[FallEventRecord, float | None] | None] = queue.Queue()
        self._worker: threading.Thread | None = None

    def report(self, rec: FallEventRecord, down_for_s: float | None = None) -> None:
        """Queue a report; returns immediately."""
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, daemon=True, name="fall-reporter")
            self._worker.start()
        self._queue.put((rec, down_for_s))

    def report_now(self, rec: FallEventRecord, down_for_s: float | None = None) -> bool:
        """Send with retries on the calling thread. True if the main service accepted it."""
        payload = alert_payload(rec, down_for_s)
        for attempt in range(RETRIES):
            try:
                r = self._client.post(self.url, json=payload)
                if r.status_code < 400:
                    log.info("reported %s -> alert %s", rec.event_id, r.json().get("id"))
                    return True
                if r.status_code < 500:  # the contract rejected it: retrying will not help
                    log.error("alert rejected (%s): %s", r.status_code, r.text[:300])
                    return False
                log.warning("alert POST failed (%s), attempt %d", r.status_code, attempt + 1)
            except httpx.HTTPError as e:
                log.warning("alert POST error: %s, attempt %d", e, attempt + 1)
            if attempt < RETRIES - 1:
                time.sleep(self._backoff[min(attempt, len(self._backoff) - 1)])
        log.error("could not report %s to %s; it stays in the event store", rec.event_id, self.url)
        return False

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            self.report_now(*item)

    def close(self) -> None:
        if self._worker and self._worker.is_alive():
            self._queue.put(None)
            self._worker.join(timeout=2)
        self._client.close()
