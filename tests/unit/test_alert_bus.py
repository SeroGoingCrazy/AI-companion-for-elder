import asyncio
import threading

import pytest

from elder_companion.alerts.bus import AlertBus

pytestmark = pytest.mark.unit


async def test_publish_reaches_every_subscriber() -> None:
    bus = AlertBus()
    with bus.subscribe() as a, bus.subscribe() as b:
        assert bus.subscriber_count == 2
        assert bus.publish("x") == 2
        assert await asyncio.wait_for(a.get(), 1) == "x"
        assert await asyncio.wait_for(b.get(), 1) == "x"
    assert bus.subscriber_count == 0
    assert bus.publish("nobody listening") == 0


async def test_publish_from_another_thread() -> None:
    bus = AlertBus()
    with bus.subscribe() as q:
        t = threading.Thread(target=bus.publish, args=("from worker",))
        t.start()
        t.join()
        assert await asyncio.wait_for(q.get(), 1) == "from worker"


async def test_full_queue_drops_instead_of_blocking() -> None:
    bus = AlertBus(maxsize=1)
    with bus.subscribe() as q:
        bus.publish(1)
        bus.publish(2)
        await asyncio.sleep(0)  # let the loop run the scheduled puts
        assert q.qsize() == 1 and q.get_nowait() == 1


def test_subscriber_on_closed_loop_is_pruned() -> None:
    bus = AlertBus()

    leaked = bus.subscribe()  # keep a reference, or GC would run its cleanup

    async def subscribe_and_leak() -> None:
        # Simulate a subscriber whose loop dies without leaving the `with` block.
        leaked.__enter__()

    asyncio.run(subscribe_and_leak())
    assert bus.subscriber_count == 1
    assert bus.publish("x") == 0
    assert bus.subscriber_count == 0
