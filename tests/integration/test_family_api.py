from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from elder_companion.db import utcnow
from elder_companion.models import Message, SymptomLog

pytestmark = pytest.mark.integration

SYMPTOM_FIELDS = {
    "id",
    "canonical",
    "label",
    "display_en",
    "display_zh",
    "red_flag",
    "body_part",
    "severity",
    "duration",
    "onset",
    "status",
    "raw_quote",
    "count",
    "first_seen",
    "last_seen",
}


def _add_symptom(c: TestClient, canonical: str, days_ago: float, **kw) -> None:
    ts = utcnow() - timedelta(days=days_ago)
    with c.app.state.sessionmaker() as s:
        s.add(
            SymptomLog(
                elder_id=1,
                canonical=canonical,
                label=kw.pop("label", canonical),
                raw_quote=kw.pop("raw_quote", "quote"),
                first_seen=ts,
                last_seen=ts,
                **kw,
            )
        )
        s.commit()


def test_symptoms_grouped_by_day(client: TestClient) -> None:
    _add_symptom(client, "chest_pain", 0, raw_quote="胸口闷", count=2)
    _add_symptom(client, "joint_pain", 3, severity="moderate", status="ongoing")
    _add_symptom(client, "insomnia", 10)  # outside 7 days

    r = client.get("/api/symptoms?days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["timezone"] == "America/Los_Angeles"
    days = body["days"]
    assert len(days) == 7
    assert days[0]["label"] == "Today"
    all_rows = [s for d in days for s in d["symptoms"]]
    assert sorted(s["canonical"] for s in all_rows) == ["chest_pain", "joint_pain"]

    chest = next(s for s in all_rows if s["canonical"] == "chest_pain")
    assert set(chest) == SYMPTOM_FIELDS
    assert chest["display_en"] == "Chest pain" and chest["display_zh"] == "胸痛胸闷"
    assert chest["red_flag"] is True and chest["count"] == 2 and chest["raw_quote"] == "胸口闷"
    assert chest["last_seen"].endswith(("Z", "+00:00"))  # browsers must not read it as local


def test_symptoms_days_bounds(client: TestClient) -> None:
    assert len(client.get("/api/symptoms?days=1").json()["days"]) == 1
    assert client.get("/api/symptoms?days=0").status_code == 422
    assert client.get("/api/symptoms?days=99").status_code == 422
    assert client.get("/api/symptoms?elder_id=42").status_code == 404


def test_messages_oldest_first_with_paging(client: TestClient) -> None:
    with client.app.state.sessionmaker() as s:
        s.add_all(Message(elder_id=1, role="user", text=f"m{i}") for i in range(5))
        s.commit()
    msgs = client.get("/api/messages?limit=3").json()
    assert [m["text"] for m in msgs] == ["m2", "m3", "m4"]
    assert set(msgs[0]) == {"id", "role", "text", "private", "created_at"}
    older = client.get(f"/api/messages?limit=3&before_id={msgs[0]['id']}").json()
    assert [m["text"] for m in older] == ["m0", "m1"]


def test_mark_alert_read(client: TestClient) -> None:
    alert = client.post(
        "/api/alerts", json={"type": "fall", "level": "high", "title": "Fall detected"}
    ).json()
    assert alert["created_at"].endswith(("Z", "+00:00"))
    r = client.post(f"/api/alerts/{alert['id']}/read")
    assert r.status_code == 200 and r.json()["is_read"] is True
    assert client.get("/api/alerts").json()[0]["is_read"] is True
    assert client.post("/api/alerts/999/read").status_code == 404


def test_today_summary(client: TestClient) -> None:
    empty = client.get("/api/summary/today").json()
    assert empty["empty"] is True and empty["summary"] == "No conversations yet today."
    client.post("/api/chat", json={"text": "I watered the roses"})
    body = client.get("/api/summary/today").json()
    assert set(body) == {"summary", "generated_at", "fallback", "empty", "has_private"}
    assert body["empty"] is False and body["summary"].startswith("Maggie")
    again = client.get("/api/summary/today").json()
    assert again["generated_at"] == body["generated_at"]  # cached
    refreshed = client.get("/api/summary/today?refresh=true").json()
    assert refreshed["generated_at"] != body["generated_at"]
