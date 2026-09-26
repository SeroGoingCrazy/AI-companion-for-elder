"""In-process alert broadcast for the dashboard's SSE stream (spec 5.4).

Each SSE connection subscribes with its own asyncio.Queue. publish() is called from sync code
running in worker threads (routes, background tasks), so it hands items to each subscriber's
event loop with call_soon_threadsafe instead of touching the queues directly.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


def _put(queue: asyncio.Queue[Any], item: Any) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        # A stalled client must not grow memory without bound; it reloads alerts on reconnect.
        logger.warning("alert subscriber queue full; dropping an alert for it")


class AlertBus:
    def __init__(self, maxsize: int = 100) -> None:
        self._maxsize = maxsize
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[Any]]] = set()
        self._lock = threading.Lock()

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[Any]]:
        """Queue receiving every published item until the block exits. Needs a running loop."""
        entry = (asyncio.get_running_loop(), asyncio.Queue(self._maxsize))
        with self._lock:
            self._subscribers.add(entry)
        try:
            yield entry[1]
        finally:
            with self._lock:
                self._subscribers.discard(entry)

    def publish(self, item: Any) -> int:
        """Deliver `item` to every subscriber; safe from any thread. Returns how many got it."""
        with self._lock:
            subscribers = list(self._subscribers)
        delivered = 0
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(_put, queue, item)
                delivered += 1
            except RuntimeError:  # that subscriber's loop is closed
                with self._lock:
                    self._subscribers.discard((loop, queue))
        return delivered
