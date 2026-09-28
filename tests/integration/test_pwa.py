"""The app is installable to a phone home screen, and the worker leaves live data alone."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from elder_companion.web.routes.pwa import MANIFEST_MEDIA_TYPE

pytestmark = pytest.mark.integration

MANIFESTS = ["/manifest-elder.webmanifest", "/manifest-family.webmanifest"]


@pytest.mark.parametrize("path", MANIFESTS)
def test_manifest_is_served_with_the_manifest_media_type(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(MANIFEST_MEDIA_TYPE)


@pytest.mark.parametrize("path", MANIFESTS)
def test_manifest_meets_the_installability_bar(client: TestClient, path: str) -> None:
    m = client.get(path).json()
    assert m["display"] == "standalone"
    assert m["scope"] == "/"
    assert m["start_url"] in {"/elder", "/family"}

    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(i["purpose"] == "maskable" for i in m["icons"])


def test_the_two_manifests_are_separately_installable(client: TestClient) -> None:
    elder, family = (client.get(p).json() for p in MANIFESTS)
    assert elder["id"] != family["id"]
    assert elder["start_url"] == "/elder"
    assert family["start_url"] == "/family"


def test_manifest_follows_the_configured_companion_name(client: TestClient) -> None:
    name = client.app.state.settings.chat.companion_name
    assert client.get(MANIFESTS[0]).json()["short_name"] == name


@pytest.mark.parametrize("path", MANIFESTS)
def test_manifest_icons_exist(client: TestClient, path: str) -> None:
    for icon in client.get(path).json()["icons"]:
        r = client.get(icon["src"])
        assert r.status_code == 200, icon["src"]
        assert r.headers["content-type"] == "image/png"


def test_apple_touch_icon_exists(client: TestClient) -> None:
    assert client.get("/static/icons/apple-touch-icon.png").status_code == 200


def test_service_worker_is_served_from_the_root_with_a_root_scope(client: TestClient) -> None:
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    # Without this, a worker at /sw.js could still only claim /, but say it explicitly.
    assert r.headers["service-worker-allowed"] == "/"
    # A cached worker script pins the whole app to an old build.
    assert "no-cache" in r.headers["cache-control"]


def test_cache_version_follows_the_assets(client: TestClient) -> None:
    """A hand-kept version number is the one everyone forgets to bump, and every phone that
    already opened the app then keeps the previous build."""
    import re

    def version(c: TestClient) -> str:
        m = re.search(r'const VERSION = "([^"]+)"', c.get("/sw.js").text)
        assert m, "VERSION not found in sw.js"
        assert "__BUILD__" not in m.group(1), "placeholder was not substituted"
        return m.group(1)

    before = version(client)
    assert version(client) == before, "stable while nothing changes"

    from elder_companion.web.routes.pages import WEB_DIR

    # No cache_clear: an edit while the server is running has to be picked up on its own,
    # or phones keep the old stylesheet until someone restarts it.
    probe = WEB_DIR / "static" / "_version_probe.tmp"
    probe.write_text("x", encoding="utf-8")
    try:
        assert version(client) != before, "an asset changed but the cache name did not"
    finally:
        probe.unlink()
    assert version(client) == before


def test_service_worker_never_caches_live_data(client: TestClient) -> None:
    """A cached alert or a replayed chat reply is worse than no worker at all."""
    sw = client.get("/sw.js").text
    bypass = re.search(r"const BYPASS = \[(.+?)\];", sw, re.S)
    assert bypass, "BYPASS list not found in sw.js"
    for path in ("/api/", "/media/"):
        assert path.replace("/", r"\/") in bypass.group(1)


def test_service_worker_precache_list_resolves(client: TestClient) -> None:
    sw = client.get("/sw.js").text
    assets = re.findall(r'"(/static/[^"]+)"', sw)
    assert assets, "no precached assets found in sw.js"
    for url in assets:
        assert client.get(url).status_code == 200, url


@pytest.mark.parametrize(
    ("page", "manifest"),
    [("/elder", "/manifest-elder.webmanifest"), ("/family", "/manifest-family.webmanifest")],
)
def test_pages_link_their_manifest_and_register_the_worker(
    client: TestClient, page: str, manifest: str
) -> None:
    html = client.get(page).text
    assert f'rel="manifest" href="{manifest}"' in html
    assert "/static/pwa.js" in html
    # iOS ignores the manifest entirely and needs these two.
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'rel="apple-touch-icon"' in html


@pytest.mark.parametrize("path", MANIFESTS)
def test_manifest_is_valid_json_without_trailing_content(client: TestClient, path: str) -> None:
    json.loads(client.get(path).content)
