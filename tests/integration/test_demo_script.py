"""G4: the demo script (DEV_SPEC "Demo Script") replays offline under LLM_PROVIDER=mock.

Steps 5, 7 and 8 need fall-mcp / E5 and are covered by their own tests.
"""

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from elder_companion.llm.mock import MOCK_AUDIO_PREFIX
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

    # 2. Sleep (spoken: the mock transcribes b"MOCK:<text>")
    line = "Much better today, but I didn't sleep well last night."
    r = c.post(
        "/api/chat/audio",
        files={"audio": ("blob", MOCK_AUDIO_PREFIX + line.encode(), "audio/webm")},
    ).json()
    assert r["user_text"] == line and "night" in r["reply_text"]
    assert "insomnia" in _today(c)

    # 3. Dizziness: on the timeline, no alert
    r = c.post("/api/chat", json={"text": "这两天早上起来头有点晕"}).json()
    assert "头晕" in r["reply_text"]
    assert "dizziness" in _today(c)
    assert [a for a in c.get("/api/alerts").json() if not a["is_read"]] == []

    # 4. Chest tightness: 911 advice + high alerts
    r = c.post("/api/chat", json={"text": "My chest feels tight and I can't catch my breath"})
    assert "911" in r.json()["reply_text"]
    unread = [a for a in c.get("/api/alerts").json() if not a["is_read"]]
    assert {(a["level"], a["title"]) for a in unread} == {
        ("high", "Chest pain"),
        ("high", "Shortness of breath"),
    }

    # 6. Summary mentions the dizziness and the emergency
    summary = c.get("/api/summary/today?refresh=true").json()
    assert not summary["empty"] and not summary["fallback"]
    assert "dizzy" in summary["summary"] and "emergency" in summary["summary"]

    # TTS is off in mock mode: the page falls back to the browser's voice
    assert c.get(f"/api/tts/{r.json()['message_id']}").status_code == 204
