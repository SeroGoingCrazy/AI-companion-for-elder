"""The family's conversation view never shows personal details (redaction.py, mock LLM)."""

import pytest
from fastapi.testclient import TestClient

from elder_companion.models import Message

pytestmark = pytest.mark.integration

SECRET_TURN = "My bank PIN is 4821 and Amy's number is 555-123-4567, I wrote it by the phone."
SECRETS = ("4821", "555-123-4567")


def test_messages_endpoint_redacts_personal_details(client: TestClient) -> None:
    client.post("/api/chat", json={"text": SECRET_TURN})
    msgs = client.get("/api/messages?limit=50").json()
    texts = [m["text"] for m in msgs]
    assert not [t for t in texts if any(s in t for s in SECRETS)]
    assert any("[password]" in t and "[phone number]" in t for t in texts)
    assert any("I wrote it by the phone" in t for t in texts)  # the rest is kept


def test_turn_is_redacted_in_the_background(client: TestClient) -> None:
    r = client.post("/api/chat", json={"text": SECRET_TURN}).json()
    with client.app.state.sessionmaker() as s:
        user = s.get(Message, r["message_id"] - 1)
        reply = s.get(Message, r["message_id"])
        assert user.text == SECRET_TURN  # the companion still has her words
        assert user.family_text is not None and "4821" not in user.family_text
        assert reply.family_text is not None


def test_greeting_is_redacted_in_the_background(client: TestClient) -> None:
    r = client.post("/api/chat/greet", json={}).json()
    with client.app.state.sessionmaker() as s:
        assert s.get(Message, r["message_id"]).family_text is not None


def _digest_prompts(llm) -> list[str]:  # noqa: ANN001
    """Since H7 the day is summarized by a structured `daily_digest` call, not a chat."""
    return [
        c["messages"][-1]["content"]
        for name, c in llm.calls
        if name == "extract_json" and c["name"] == "daily_digest"
    ]


def test_summary_never_sees_personal_details(client: TestClient) -> None:
    client.post("/api/chat", json={"text": SECRET_TURN})
    client.get("/api/summary/today?refresh=true")
    prompts = _digest_prompts(client.app.state.llm)
    assert prompts
    assert not [p for p in prompts if any(s in p for s in SECRETS)]


def test_weekly_report_never_sees_personal_details(client: TestClient) -> None:
    """The weekly page builds today's digest too, so it goes through the same scrubbing."""
    client.post("/api/chat", json={"text": SECRET_TURN})
    assert client.get("/family/report/weekly").status_code == 200
    prompts = _digest_prompts(client.app.state.llm)
    assert prompts
    assert not [p for p in prompts if any(s in p for s in SECRETS)]
    assert not [s for s in SECRETS if s in client.get("/family/report/weekly").text]
