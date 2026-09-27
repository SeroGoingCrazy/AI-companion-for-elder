"""H3/H4: sessions, the greeting's agenda, the first-chat disclosure and private memory in
the companion's own context (mock LLM)."""

from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from elder_companion.chat.context import PRIVATE_TAG
from elder_companion.llm import LLMError
from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import ChatSession, Elder, MemoryItem, Message, Reminder
from elder_companion.seed import seed_history
from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def llm() -> MockLLMClient:
    return MockLLMClient.from_config()


@pytest.fixture
def client(settings: Settings, llm: MockLLMClient) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=llm)) as c:
        yield c


def _greet_instruction(llm: MockLLMClient) -> str:
    return [args for name, args in llm.calls if name == "chat"][-1][-1]["content"]


def _system_prompt(llm: MockLLMClient) -> str:
    return [args for name, args in llm.calls if name == "chat"][-1][0]["content"]


def _no_reminders(c: TestClient) -> None:
    """These tests are about follow-ups. The seeded pill reminder outranks them (spec 3.8)
    and is due only after 08:00, which would make the ordering depend on the wall clock;
    reminder priority has its own tests in test_reminders_api.py."""
    with c.app.state.sessionmaker() as s:
        for reminder in s.scalars(select(Reminder)):
            reminder.active = False
        s.commit()


def _add_follow_ups(c: TestClient, subjects: list[str], today) -> None:  # noqa: ANN001
    with c.app.state.sessionmaker() as s:
        for i, subject in enumerate(subjects):
            s.add(
                MemoryItem(
                    elder_id=1,
                    kind="follow_up",
                    subject=subject,
                    text=f"{subject} plan",
                    raw_quote=subject,
                    due_date=today - timedelta(days=len(subjects) - i),
                    status="open",
                )
            )
        s.commit()


def test_first_greeting_discloses_the_privacy_rule_once(client: TestClient, llm) -> None:  # noqa: ANN001
    r = client.post("/api/chat/greet").json()
    assert "first chat together" in _greet_instruction(llm) and "Amy" in _greet_instruction(llm)
    assert "between us" in r["reply_text"] and "Amy" in r["reply_text"]
    client.post("/api/chat/greet")
    assert "first chat together" not in _greet_instruction(llm)
    with client.app.state.sessionmaker() as s:
        assert s.get_one(Elder, 1).privacy_disclosed_at is not None


def test_seeded_orchid_is_asked_once(settings: Settings, client: TestClient, llm) -> None:  # noqa: ANN001
    with client.app.state.sessionmaker() as s:
        seed_history(s, datetime.now(settings.chat.tz))
    _no_reminders(client)
    r = client.post("/api/chat/greet").json()
    assert "orchid" in r["reply_text"]
    assert "1. orchid" in _greet_instruction(llm)
    r = client.post("/api/chat/greet").json()
    assert "orchid" not in _greet_instruction(llm) and "knee" in r["reply_text"]
    with client.app.state.sessionmaker() as s:
        orchid = s.scalars(select(MemoryItem).where(MemoryItem.subject == "orchid")).one()
        assert orchid.status == "asked"


def test_budget_of_three_in_priority_order_rest_carries_over(
    settings: Settings,
    client: TestClient,
    llm,  # noqa: ANN001
) -> None:
    today = datetime.now(settings.chat.tz).date()
    _no_reminders(client)
    _add_follow_ups(client, ["eye doctor", "choir", "Leo's exam", "pie"], today)
    client.post("/api/chat/greet")
    instruction = _greet_instruction(llm)
    assert "1. eye doctor" in instruction and "2. choir" in instruction
    assert "3. Leo's exam" in instruction and "pie" not in instruction
    client.post("/api/chat/greet")
    assert "1. pie" in _greet_instruction(llm)


def test_fallback_greeting_does_not_deliver_the_agenda(settings: Settings) -> None:
    class DownLLM(MockLLMClient):
        def chat(self, messages, *, max_tokens=None):  # noqa: ANN001
            raise LLMError("down")

    with TestClient(create_app(settings, llm=DownLLM.from_config())) as c:
        _no_reminders(c)
        _add_follow_ups(c, ["orchid"], datetime.now(settings.chat.tz).date())
        assert c.post("/api/chat/greet").json()["fallback"]
        with c.app.state.sessionmaker() as s:
            assert s.scalars(select(MemoryItem)).one().status == "open"
            assert s.get_one(Elder, 1).privacy_disclosed_at is None


def test_messages_belong_to_the_greeting_session(client: TestClient) -> None:
    client.post("/api/chat", json={"text": "Hello"})  # no greeting yet: opens a session
    client.post("/api/chat/greet")
    client.post("/api/chat", json={"text": "I watered the roses"})
    with client.app.state.sessionmaker() as s:
        sessions = s.scalars(select(ChatSession.id).order_by(ChatSession.id)).all()
        msgs = s.scalars(select(Message).order_by(Message.id)).all()
    assert len(sessions) == 2
    assert [m.session_id for m in msgs] == [sessions[0]] * 2 + [sessions[1]] * 3


def test_the_companion_still_remembers_private_things(client: TestClient, llm) -> None:  # noqa: ANN001
    client.post("/api/chat/greet")
    r = client.post(
        "/api/chat",
        json={"text": "My friend Linda got bad news from her doctor. Keep this between us, okay?"},
    ).json()
    assert "between us" in r["reply_text"] and "Amy" in r["reply_text"]  # in-the-moment disclosure
    client.post("/api/chat/greet")
    system = _system_prompt(llm)
    [line] = [x for x in system.splitlines() if x.startswith("- Linda")]
    assert PRIVATE_TAG in line
