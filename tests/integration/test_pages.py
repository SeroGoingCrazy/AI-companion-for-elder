import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_root_redirects_to_elder_app(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/elder"


def test_elder_page_renders_persona_and_config(client: TestClient) -> None:
    r = client.get("/elder")
    assert r.status_code == 200
    html = r.text
    assert "<title>Sunny</title>" in html
    assert "Hello, Maggie!" in html
    assert 'id="mic-btn"' in html
    assert 'window.APP_CONFIG = {"elderId": 1, "nickname": "Maggie"}' in html
    assert "call 911" in html


def test_elder_page_unknown_elder_404(client: TestClient) -> None:
    assert client.get("/elder?elder_id=99").status_code == 404


@pytest.mark.parametrize("path", ["/static/elder.js", "/static/style.css"])
def test_static_assets_served(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200
