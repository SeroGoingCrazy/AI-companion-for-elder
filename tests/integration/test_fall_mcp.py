"""F6/F7: fall-mcp tools over an in-process MCP client, plus the HTTP routes and MJPEG stream."""

from __future__ import annotations

import base64
import time
from pathlib import Path

import pytest

pytest.importorskip("cv2")

from mcp import Client  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from fall_detector.config import DEFAULT_CONFIG_PATH, FallConfig, MCPConfig, load_config  # noqa: E402
from fall_detector.events import EventStore  # noqa: E402
from fall_detector.mcp_tools import build_mcp  # noqa: E402
from fall_detector.monitor import MonitorController  # noqa: E402
from fall_detector.server import create_app  # noqa: E402
from fall_detector.stream import mjpeg_generator  # noqa: E402
from tests.fall_fakes import FakeEstimator, FakeSource  # noqa: E402

pytestmark = pytest.mark.integration

TOOLS = {"get_fall_events", "get_event_snapshot", "get_monitor_status", "start_monitoring",
         "stop_monitoring", "analyze_video"}


@pytest.fixture
def cfg(tmp_path: Path) -> FallConfig:
    return FallConfig(mcp=MCPConfig(events_path=tmp_path / "ev.jsonl", snapshots_dir=tmp_path / "snap"))


@pytest.fixture
def controller(cfg: FallConfig) -> MonitorController:
    store = EventStore(cfg.mcp.events_path, cfg.mcp.snapshots_dir)
    c = MonitorController(cfg, store, None, estimator_factory=FakeEstimator, source_factory=FakeSource)
    yield c
    c.close()


@pytest.fixture
def mcp(controller: MonitorController):
    return build_mcp(controller, controller.store)


def _wait(cond, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


async def test_lists_all_tools(mcp) -> None:
    async with Client(mcp) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
    assert tools == TOOLS


async def test_analyze_then_query_events_and_snapshot(mcp) -> None:
    async with Client(mcp) as c:
        r = await c.call_tool("analyze_video", {"path": "fake:fall"})
        assert not r.is_error
        [ev] = r.structured_content["events"]
        assert (await c.call_tool("analyze_video", {"path": "fake:lie_down"})).structured_content["events"] == []

        # offline events are hidden unless asked for
        live = await c.call_tool("get_fall_events", {})
        assert live.structured_content["result"] == []
        both = await c.call_tool("get_fall_events", {"include_offline": True})
        assert [e["event_id"] for e in both.structured_content["result"]] == [ev["event_id"]]

        snap = await c.call_tool("get_event_snapshot", {"event_id": ev["event_id"]})
        assert not snap.is_error
        image = next(b for b in snap.content if b.type == "image")
        assert image.mime_type == "image/jpeg"
        assert base64.b64decode(image.data)[:2] == b"\xff\xd8"


async def test_live_monitoring_through_tools(mcp, controller: MonitorController) -> None:
    async with Client(mcp) as c:
        r = await c.call_tool("start_monitoring", {"source": "fake:fall", "loop": False})
        assert r.structured_content == {"running": True, "source": "fake:fall", "loop": False}
        _wait(lambda: controller.status().ended)
        status = (await c.call_tool("get_monitor_status", {})).structured_content
        assert status["events"] == 1 and status["states"] == {"1": "DOWN"}
        events = (await c.call_tool("get_fall_events", {"since": "2020-01-01T00:00:00Z"})).structured_content
        assert len(events["result"]) == 1
        assert (await c.call_tool("stop_monitoring", {})).structured_content == {"running": False}


async def test_errors_are_readable_tool_errors(mcp) -> None:
    async with Client(mcp) as c:
        for name, args, text in [
            ("analyze_video", {"path": "demo/videos/nope.mp4"}, "not found"),
            ("analyze_video", {"path": "0"}, "video file"),
            ("start_monitoring", {"source": "missing.mp4"}, "not found"),
            ("get_event_snapshot", {"event_id": "fall_x"}, "get_fall_events"),
            ("get_fall_events", {"since": "yesterday"}, "ISO 8601"),
        ]:
            r = await c.call_tool(name, args)
            assert r.is_error, name
            assert text in r.content[0].text, (name, r.content[0].text)


def test_http_routes(cfg: FallConfig, controller: MonitorController) -> None:
    app = create_app(cfg, controller, autostart="fake:walk")
    with TestClient(app) as http:
        health = http.get("/healthz").json()
        assert health["ok"] and health["monitor"]["source"] == "fake:walk"
        assert http.post("/control/start", json={"source": "bad.mp4"}).status_code == 400
        r = http.post("/control/start", json={"source": "fake:fall", "loop": False})
        assert r.status_code == 200 and r.json()["source"] == "fake:fall"
        assert http.post("/control/stop").json()["running"] is False
        assert http.get("/control/status").json()["running"] is False


async def test_mjpeg_stream_parts(controller: MonitorController) -> None:
    gen = mjpeg_generator(controller)
    idle = await anext(gen)  # nothing running yet: the paused placeholder
    assert idle.startswith(b"--frame\r\nContent-Type: image/jpeg")
    controller.start("fake:walk", loop=True)
    _wait(lambda: controller.latest_frame()[1] is not None)
    part = await anext(gen)
    jpeg = part.split(b"\r\n\r\n", 1)[1]
    assert jpeg.startswith(b"\xff\xd8") and part != idle
    await gen.aclose()


def test_config_reads_fall_section(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FALL_SOURCE", "0")
    monkeypatch.setenv("FALL_DEVICE", "mps")
    cfg = load_config(DEFAULT_CONFIG_PATH, load_env_file=False)
    assert cfg.mcp.autostart_source == "0" and cfg.device == "mps"
    assert cfg.rules.down_confirm_s == 3 and cfg.mcp.port == 8001
    assert cfg.mcp.events_path.is_absolute()
    assert cfg.model_path.endswith("yolo11n-pose.pt")
