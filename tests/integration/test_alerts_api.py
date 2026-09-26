import pytest
from fastapi.testclient import TestClient

from elder_companion.models import Alert

pytestmark = pytest.mark.integration

FALL_ALERT = {
    "type": "fall",
    "level": "high",
    "title": "Fall detected",
    "content": "Person down for 3s in living room",
    "snapshot_path": "snapshots/20261001_101530.jpg",
    "ref_id": "evt_001",
}


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"


def test_post_fall_alert_contract(client: TestClient) -> None:
    r = client.post("/api/alerts", json=FALL_ALERT)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] > 0
    assert body["elder_id"] == 1
    assert body["is_read"] is False
    for k, v in FALL_ALERT.items():
        assert body[k] == v

    with client.app.state.sessionmaker() as s:
        row = s.get(Alert, body["id"])
        assert row is not None and row.type == "fall" and row.ref_id == "evt_001"


def test_minimal_payload(client: TestClient) -> None:
    r = client.post("/api/alerts", json={"type": "symptom", "level": "medium", "title": "Dizzy"})
    assert r.status_code == 201
    assert r.json()["snapshot_path"] is None


def test_list_newest_first(client: TestClient) -> None:
    client.post("/api/alerts", json={**FALL_ALERT, "title": "first"})
    client.post("/api/alerts", json={**FALL_ALERT, "title": "second"})
    titles = [a["title"] for a in client.get("/api/alerts").json()]
    assert titles[:2] == ["second", "first"]


def test_windows_snapshot_path_normalized(client: TestClient) -> None:
    r = client.post("/api/alerts", json={**FALL_ALERT, "snapshot_path": "snapshots\\a.jpg"})
    assert r.status_code == 201
    assert r.json()["snapshot_path"] == "snapshots/a.jpg"


@pytest.mark.parametrize(
    "patch",
    [
        {"type": "smoke"},
        {"level": "low"},
        {"title": ""},
        {"snapshot_path": "../../etc/passwd"},
        {"snapshot_path": "C:/Users/x.jpg"},
        {"snapshot_path": "/abs/x.jpg"},
        {"unexpected": "field"},
    ],
)
def test_rejects_invalid_payload(client: TestClient, patch: dict) -> None:
    assert client.post("/api/alerts", json={**FALL_ALERT, **patch}).status_code == 422


def test_unknown_elder_404(client: TestClient) -> None:
    assert client.post("/api/alerts", json={**FALL_ALERT, "elder_id": 999}).status_code == 404


def test_app_restart_is_idempotent(settings) -> None:
    from elder_companion.web.app import create_app

    for _ in range(2):
        with TestClient(create_app(settings)) as c:
            assert c.post("/api/alerts", json=FALL_ALERT).status_code == 201
    with TestClient(create_app(settings)) as c:
        assert len(c.get("/api/alerts").json()) == 2
