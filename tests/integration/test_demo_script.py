"""G4: the demo script (DEV_SPEC "Demo Script") replays offline under LLM_PROVIDER=mock.

Steps 6, 9 and 10 need fall-mcp / E5 and are covered by their own tests.
"""

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from elder_companion.llm.mock import MOCK_AUDIO_PREFIX
from elder_companion.privacy import PRIVATE_DAY_NOTE
from elder_companion.seed import seed_history
from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def demo(settings: Settings) -> Iterator[TestClient]:
    """Mock LLM + the seeded 6-day history, like `dev_up -Fresh -Mock`."""
    with TestClient(create_app(settings)) as c:
        with c.app.state.sessionmaker() as s:
            assert seed_history(s, datetime.now(settings.chat.tz))
        yield c


def _today(c: TestClient) -> list[str]:
    return [s["canonical"] for s in c.get("/api/symptoms?days=1").json()["days"][0]["symptoms"]]


def test_demo_script_replays_offline(demo: TestClient) -> None:
    c = demo
    assert c.get("/elder").status_code == 200 and c.get("/family").status_code == 200
    assert c.get("/api/summary/today").json()["empty"] is True

    # 1. Start chatting: the greeting asks how the orchid repotting went (seeded follow-up)
    greet = c.post("/api/chat/greet").json()
    assert "orchid" in greet["reply_text"] and not greet["fallback"]

    # 2. Knee better, poor sleep (spoken: the mock transcribes b"MOCK:<text>")
    line = "Much better today, but I didn't sleep well last night."
    r = c.post(
        "/api/chat/audio",
        files={"audio": ("blob", MOCK_AUDIO_PREFIX + line.encode(), "audio/webm")},
    ).json()
    assert r["user_text"] == line and "night" in r["reply_text"]
    # 2b. Asked before sharing: hidden until she says yes
    assert "let Amy know" in r["reply_text"]
    assert "insomnia" not in _today(c)
    held = [m for m in c.get("/api/messages").json() if m["awaiting_consent"]]
    assert len(held) == 2 and all(m["text"] == "" for m in held)  # her line + the question
    r = c.post("/api/chat", json={"text": "Yes, you can tell her."}).json()
    assert "Amy" in r["reply_text"]
    assert "insomnia" in _today(c)
    assert any(m["text"] == line for m in c.get("/api/messages").json())

    # 3. Dizziness: asked, agreed, then on the timeline, no alert
    r = c.post("/api/chat", json={"text": "这两天早上起来头有点晕"}).json()
    assert "头晕" in r["reply_text"] and "Amy" in r["reply_text"]
    assert "dizziness" not in _today(c)
    c.post("/api/chat", json={"text": "可以，告诉她吧"})
    assert "dizziness" in _today(c)
    assert [a for a in c.get("/api/alerts").json() if not a["is_read"]] == []

    # 4. Linda, kept between us: agreed with the safety disclosure; nothing on the dashboard
    r = c.post(
        "/api/chat",
        json={"text": "My friend Linda got bad news from her doctor. Keep this between us, okay?"},
    ).json()
    assert "between us" in r["reply_text"] and "Amy" in r["reply_text"]
    for path in ("/family", "/api/messages", "/api/care-list", "/api/symptoms"):
        assert "Linda" not in c.get(path).text, path

    # 5. Chest tightness: 911 advice + high alerts
    r = c.post("/api/chat", json={"text": "My chest feels tight and I can't catch my breath"})
    assert "911" in r.json()["reply_text"]
    unread = [a for a in c.get("/api/alerts").json() if not a["is_read"]]
    assert {(a["level"], a["title"]) for a in unread} == {
        ("high", "Chest pain"),
        ("high", "Shortness of breath"),
    }

    # 7. Summary mentions the dizziness and the emergency, plus the private note
    summary = c.get("/api/summary/today?refresh=true").json()
    assert not summary["empty"] and not summary["fallback"]
    assert "dizzy" in summary["summary"] and "emergency" in summary["summary"]
    assert summary["summary"].endswith(PRIVATE_DAY_NOTE.format(nickname="Maggie"))
    assert "Linda" not in summary["summary"]

    # 8. Ben claims the chest alert; Amy's view shows it; doctor one-pager and memoir
    members = {m["name"]: m["id"] for m in c.get("/api/family/members").json()}
    chest = next(a for a in unread if a["title"] == "Chest pain")
    c.post(
        "/api/claims",
        json={
            "target_type": "alert",
            "target_id": chest["id"],
            "member_id": members["Ben"],
            "note": "I'll call her doctor",
        },
    )
    [claim] = c.get("/api/claims").json()
    assert (claim["member_name"], claim["target_id"]) == ("Ben", chest["id"])
    doctor = c.get("/family/doctor").text
    assert "Chest pain" in doctor and "头有点晕" in doctor and "Linda" not in doctor
    memoir = c.get("/family/memoir").text
    assert "When I started teaching in 1972" in memoir

    # TTS is off in mock mode: the page falls back to the browser's voice
    assert c.get(f"/api/tts/{r.json()['message_id']}").status_code == 204
