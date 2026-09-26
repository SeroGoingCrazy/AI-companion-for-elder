"""F6: MonitorController with a scripted pose source (no YOLO, no real video)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("cv2")  # annotation / JPEG encoding needs the vision extra

from fall_detector.config import FallConfig, MCPConfig  # noqa: E402
from fall_detector.events import EventStore  # noqa: E402
from fall_detector.monitor import MonitorController  # noqa: E402
from fall_detector.reporter import Reporter  # noqa: E402
from fall_detector.sources import SourceError  # noqa: E402
from tests.fall_fakes import FakeEstimator, FakeSource  # noqa: E402

pytestmark = pytest.mark.unit


def wait_for(cond, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            raise AssertionError("timed out")
        time.sleep(0.02)


@pytest.fixture
def posted() -> list[dict]:
    return []


@pytest.fixture
def controller(tmp_path: Path, posted: list[dict]) -> MonitorController:
    cfg = FallConfig(mcp=MCPConfig(events_path=tmp_path / "ev.jsonl", snapshots_dir=tmp_path / "snap"))
    store = EventStore(cfg.mcp.events_path, cfg.mcp.snapshots_dir)

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(201, json={"id": len(posted)})

    reporter = Reporter("http://main/api/alerts", client=httpx.Client(transport=httpx.MockTransport(handler)))
    c = MonitorController(cfg, store, reporter, estimator_factory=FakeEstimator, source_factory=FakeSource)
    yield c
    c.close()


def test_live_fall_is_stored_reported_and_streamed(controller: MonitorController, posted: list[dict]) -> None:
    status = controller.start("fake:fall", loop=False)
    assert status.running and status.source == "fake:fall"
    wait_for(lambda: controller.status().ended)
    s = controller.status()
    assert s.events == 1 and not s.running
    assert s.states == {1: "DOWN"}
    [rec] = controller.store.query()
    assert rec.source == "live" and rec.video == "fake:fall"
    assert controller.store.snapshot_path(rec.event_id).read_bytes()[:2] == b"\xff\xd8"
    wait_for(lambda: len(posted) == 1)
    assert posted[0]["ref_id"] == rec.event_id
    assert posted[0]["snapshot_path"] == f"snapshots/{rec.event_id}.jpg"
    version, jpeg = controller.latest_frame()
    assert version > 0 and jpeg[:2] == b"\xff\xd8"


def test_lying_down_is_quiet(controller: MonitorController, posted: list[dict]) -> None:
    controller.start("fake:lie_down", loop=False)
    wait_for(lambda: controller.status().ended)
    assert controller.status().events == 0
    assert controller.store.query() == [] and posted == []


def test_loop_resets_tracking(controller: MonitorController) -> None:
    controller.start("fake:walk", loop=True)
    wait_for(lambda: controller.status().frames > 200)  # walk is 90 frames: looped twice
    assert controller.status().running
    assert controller._live_estimator.resets >= 3  # start + each loop


def test_bad_source_keeps_current_monitoring(controller: MonitorController) -> None:
    controller.start("fake:walk", loop=True)
    with pytest.raises(SourceError):
        controller.start("missing.mp4")
    s = controller.status()
    assert s.running and s.source == "fake:walk"


def test_stop(controller: MonitorController) -> None:
    controller.start("fake:walk", loop=True)
    wait_for(lambda: controller.status().frames > 0)
    s = controller.stop()
    assert not s.running
    frames = controller.status().frames
    time.sleep(0.1)
    assert controller.status().frames == frames


def test_switching_source_restarts(controller: MonitorController) -> None:
    controller.start("fake:walk", loop=True)
    controller.start("fake:fall", loop=False)
    wait_for(lambda: controller.status().ended)
    assert controller.status().source == "fake:fall"
    assert controller.status().events == 1


def test_analyze_video_is_offline(controller: MonitorController, posted: list[dict]) -> None:
    result = controller.analyze_video("fake:fall")
    assert result["frames"] == 162 and result["duration_s"] == pytest.approx(5.37, abs=0.01)
    [ev] = result["events"]
    assert ev["source"] == "offline" and ev["snapshot_id"]
    assert controller.analyze_video("fake:lie_down")["events"] == []
    time.sleep(0.05)
    assert posted == []  # offline analysis never alerts


def test_analyze_rejects_camera_and_missing(controller: MonitorController) -> None:
    with pytest.raises(SourceError):
        controller.analyze_video("0")
    with pytest.raises(SourceError):
        controller.analyze_video("nope.mp4")
