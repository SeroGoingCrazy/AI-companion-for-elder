"""H9: family members and claims ("Ben is handling this")."""

import pytest
from fastapi.testclient import TestClient

from elder_companion.models import MemoryItem

pytestmark = pytest.mark.integration


def _chest_alert(client: TestClient) -> int:
    client.post("/api/chat", json={"text": "My chest feels tight and I can't catch my breath"})
    return next(a["id"] for a in client.get("/api/alerts").json() if a["title"] == "Chest pain")


def test_members_are_listed(client: TestClient) -> None:
    body = client.get("/api/family/members").json()
    assert [(m["name"], m["relation"]) for m in body] == [("Amy", "daughter"), ("Ben", "son")]


def test_ben_claims_an_alert_and_marks_it_done(client: TestClient) -> None:
    alert_id = _chest_alert(client)
    ben = client.get("/api/family/members").json()[1]["id"]
    r = client.post(
        "/api/claims",
        json={
            "target_type": "alert",
            "target_id": alert_id,
            "member_id": ben,
            "note": "I'll call her doctor",
        },
    )
    assert r.status_code == 201
    claim = r.json()
    assert (claim["member_name"], claim["note"], claim["done_at"]) == (
        "Ben",
        "I'll call her doctor",
        None,
    )
    [seen] = client.get("/api/claims").json()  # what Amy's view loads
    assert seen["id"] == claim["id"] and seen["target_id"] == alert_id
    done = client.post(f"/api/claims/{claim['id']}/done").json()
    assert done["done_at"] is not None
    assert client.get("/api/claims").json()[0]["done_at"] == done["done_at"]


def test_unknown_member_target_or_claim_is_404(client: TestClient) -> None:
    alert_id = _chest_alert(client)
    body = {"target_type": "alert", "target_id": alert_id, "member_id": 99}
    assert client.post("/api/claims", json=body).status_code == 404
    body = {"target_type": "symptom_log", "target_id": 99, "member_id": 1}
    assert client.post("/api/claims", json=body).status_code == 404
    body = {"target_type": "reminder", "target_id": 1, "member_id": 1}
    assert client.post("/api/claims", json=body).status_code == 422
    assert client.post("/api/claims/99/done").status_code == 404


def test_private_memory_items_cannot_be_claimed(client: TestClient) -> None:
    with client.app.state.sessionmaker() as s:
        item = MemoryItem(
            elder_id=1, kind="person", subject="Linda", text="t", raw_quote="Linda", private=True
        )
        s.add(item)
        s.commit()
        item_id = item.id
    body = {"target_type": "memory_item", "target_id": item_id, "member_id": 1}
    assert client.post("/api/claims", json=body).status_code == 404
