"""The fall view is served from the app's own origin, so a phone can see it.

These tests must not depend on whether a real fall-mcp happens to be running on the
developer's machine: the first version of this file hung forever because one was, and the
endless MJPEG body never completed. Each test points the app at a port it controls.
"""

import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.integration

BOUNDARY = "frame"
FRAME = b"\xff\xd8fake-jpeg\xff\xd9"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _Handler(BaseHTTPRequestHandler):
    """A finite stand-in for fall-mcp: two parts, then the body ends."""

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        if self.path != "/stream":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.end_headers()
        for _ in range(2):
            self.wfile.write(
                f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                f"Content-Length: {len(FRAME)}\r\n\r\n".encode()
            )
            self.wfile.write(FRAME + b"\r\n")
        self.wfile.write(f"--{BOUNDARY}--\r\n".encode())

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def upstream() -> Iterator[int]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def _client(settings: Settings, port: int) -> TestClient:
    """Settings are frozen by design, so point at `port` with a copy, not a mutation."""
    mcp = settings.fall.mcp.model_copy(update={"host": "127.0.0.1", "port": port})
    fall = settings.fall.model_copy(update={"mcp": mcp})
    return TestClient(create_app(settings.model_copy(update={"fall": fall})))


def test_page_points_at_the_same_origin(client: TestClient) -> None:
    """Not at fall-mcp's port: over HTTPS that image would be blocked as mixed content."""
    js = client.get("/static/family.js").text
    assert '"/fall/stream"' in js
    assert "cfg.fallStreamPort" not in js


def test_stream_is_relayed_with_its_content_type(settings: Settings, upstream: int) -> None:
    with _client(settings, upstream) as c, c.stream("GET", "/fall/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        assert "boundary=" in r.headers["content-type"]
        assert r.headers["cache-control"] == "no-store"
        body = b"".join(r.iter_bytes())
    assert body.count(FRAME) == 2


def test_offline_upstream_is_503_not_500(settings: Settings) -> None:
    """The dashboard shows "offline" and retries on this; a 500 would be a crash."""
    with _client(settings, _free_port()) as c:
        r = c.get("/fall/stream")
    assert r.status_code == 503
    assert "fall-mcp" in r.json()["detail"]
