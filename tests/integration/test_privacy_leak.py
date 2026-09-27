"""H4 acceptance: private words never reach any family-facing endpoint or page, while red
flags inside a private segment still alert (mock LLM)."""

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from elder_companion.seed import seed_history
from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.integration

LINDA = "My friend Linda got bad news from her doctor. Please keep this between us."
PRIVATE_WORDS = ("Linda", "bad news", "roof repair", "worried about money")

# Every family-facing read (spec 3.9). Keep this list in sync with new family routes.
FAMILY_ENDPOINTS = (
    "/family",
    "/family?member=ben",
    "/api/symptoms?days=31",
    "/api/messages?limit=200",
    "/api/summary/today?refresh=true",
    "/api/alerts?limit=200",
    "/api/care-list",
    "/api/reminders",
    "/api/claims",
    "/api/family/members",
    "/family/doctor?days=365",
    "/family/memoir",
)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        with c.app.state.sessionmaker() as s:
            seed_history(s, datetime.now(settings.chat.tz))  # has its own private segment
        yield c


def _leaks(c: TestClient) -> dict[str, list[str]]:
    found = {}
    for path in FAMILY_ENDPOINTS:
        r = c.get(path)
        assert r.status_code == 200, path
        if hits := [w for w in PRIVATE_WORDS if w.casefold() in r.text.casefold()]:
            found[path] = hits
    return found


def _get_routes(app) -> set[str]:  # noqa: ANN001
    """Every GET path in the app. FastAPI wraps an included router in `_IncludedRouter`,
    which exposes neither `.path` nor `.routes`, so walk `original_router` as well: reading
    only `app.routes` finds nothing and silently makes the check below pass."""
    found = set()
    stack = list(app.routes)
    while stack:
        route = stack.pop()
        if inner := getattr(route, "original_router", None):
            stack.extend(inner.routes)
        if nested := getattr(route, "routes", None):
            stack.extend(nested)
        if "GET" in (getattr(route, "methods", None) or ()):
            found.add(route.path)
    return found


def test_the_route_scan_finds_the_apps_routes(client: TestClient) -> None:
    """Guards the guard: if this ever comes back empty, the check below means nothing."""
    assert {"/api/symptoms", "/api/reminders", "/family"} <= _get_routes(client.app)


def test_family_routes_are_all_scanned(client: TestClient) -> None:
    family_paths = {
        path
        for path in _get_routes(client.app)
        if (path.startswith("/family") or path.startswith("/api/"))
        and not path.startswith(("/api/chat", "/api/tts", "/api/alerts/stream"))
    }
    scanned = {p.split("?")[0] for p in FAMILY_ENDPOINTS}
    missing = sorted(family_paths - scanned)
    assert not missing, f"not scanned for private words: {missing}"


def test_private_words_reach_no_family_view(client: TestClient) -> None:
    client.post("/api/chat/greet")
    client.post("/api/chat", json={"text": "I watered the roses this morning"})
    r = client.post("/api/chat", json={"text": LINDA}).json()
    assert "between us" in r["reply_text"] and "Amy" in r["reply_text"]
    assert _leaks(client) == {}
    summary = client.get("/api/summary/today").json()
    assert summary["has_private"]
    assert summary["summary"].endswith("Maggie asked to keep part of today's conversation private.")
    history = client.get("/api/messages").json()
    assert [m["text"] for m in history[-2:]] == ["", ""] and history[-2]["private"]
    assert history[-4]["text"] == "I watered the roses this morning"  # outside the request


def test_red_flag_in_private_segment_still_alerts_with_the_quote(client: TestClient) -> None:
    client.post(
        "/api/chat", json={"text": "Keep this between us, but I fell in the kitchen yesterday"}
    )
    [alert] = [a for a in client.get("/api/alerts").json() if a["title"] == "Fall"]
    assert alert["level"] == "high" and "I fell in the kitchen" in alert["content"]
    today = client.get("/api/symptoms?days=1").json()["days"][0]["symptoms"]
    assert [(s["canonical"], s["raw_quote"]) for s in today] == [("fall", "fell")]
    assert "I fell in the kitchen" not in client.get("/api/messages").text  # the message itself
