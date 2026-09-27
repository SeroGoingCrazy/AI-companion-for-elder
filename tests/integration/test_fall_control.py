"""Dashboard source control: /api/fall forwards to fall-mcp's /control."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


class _FakeFallMCP:
    """Stands in for fall-mcp: records calls, answers like /control does."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.offline = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("refused", request=request)
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if request.url.path.endswith("/start"):
            live = body["source"].isdigit()
            status = {"running": True, "source": body["source"], "live": live}
            return httpx.Response(200, json=status)
        return httpx.Response(200, json={"running": False, "source": None, "live": False})


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeFallMCP:
    f = _FakeFallMCP()
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(f.handler), **kwargs)

    monkeypatch.setattr("elder_companion.web.routes.fall.httpx.AsyncClient", client)
    return f


def test_camera_mode_starts_the_configured_camera(client: TestClient, fake: _FakeFallMCP) -> None:
    r = client.post("/api/fall/source", json={"mode": "camera"})
    assert r.status_code == 200
    assert r.json()["live"] is True
    assert fake.calls == [("POST", "/control/start", {"source": "0", "loop": True})]


def test_demo_mode_starts_the_demo_clip(client: TestClient, fake: _FakeFallMCP) -> None:
    client.post("/api/fall/source", json={"mode": "demo"})
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/control/start")
    assert body["source"].endswith(".mp4") and body["loop"] is True


def test_stop_and_status(client: TestClient, fake: _FakeFallMCP) -> None:
    assert client.post("/api/fall/source", json={"mode": "stop"}).json()["running"] is False
    assert client.get("/api/fall/status").status_code == 200
    assert [c[1] for c in fake.calls] == ["/control/stop", "/control/status"]


def test_unknown_mode_rejected(client: TestClient, fake: _FakeFallMCP) -> None:
    assert client.post("/api/fall/source", json={"mode": "rtsp"}).status_code == 422
    assert fake.calls == []


def test_offline_fall_service_is_503(client: TestClient, fake: _FakeFallMCP) -> None:
    fake.offline = True
    r = client.post("/api/fall/source", json={"mode": "camera"})
    assert r.status_code == 503
    assert "offline" in r.json()["error"]
