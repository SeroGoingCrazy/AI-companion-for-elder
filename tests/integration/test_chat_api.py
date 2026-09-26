from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from elder_companion.llm import LLMError
from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import Message, SymptomLog
from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.integration


class FailingLLM(MockLLMClient):
    def chat(self, messages, *, max_tokens=None):  # noqa: ANN001
        raise LLMError("network down")


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient.from_config()


@pytest.fixture
def client(settings: Settings, mock_llm: MockLLMClient) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=mock_llm)) as c:
        yield c


@pytest.fixture
def failing_client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=FailingLLM({}))) as c:
        yield c


def _messages(c: TestClient) -> list[Message]:
    with c.app.state.sessionmaker() as s:
        return list(s.scalars(select(Message).order_by(Message.id)))


def _last_chat_call(llm: MockLLMClient) -> list[dict]:
    return [args for name, args in llm.calls if name == "chat"][-1]


def test_chat_turn_persists_both_messages(client: TestClient) -> None:
    r = client.post("/api/chat", json={"text": "  I felt a bit dizzy this morning  "})
    assert r.status_code == 200
    body = r.json()
    assert body["user_text"] == "I felt a bit dizzy this morning"
    assert "dizzy" in body["reply_text"]
    assert body["fallback"] is False

    msgs = _messages(client)
    assert [(m.role, m.text) for m in msgs] == [
        ("user", "I felt a bit dizzy this morning"),
        ("assistant", body["reply_text"]),
    ]
    assert body["message_id"] == msgs[1].id


def test_chinese_input_gets_chinese_reply(client: TestClient) -> None:
    body = client.post("/api/chat", json={"text": "这两天早上起来头有点晕"}).json()
    assert "头晕" in body["reply_text"]


def test_history_is_sent_to_the_model(client: TestClient, mock_llm: MockLLMClient) -> None:
    client.post("/api/chat", json={"text": "I didn't sleep well"})
    client.post("/api/chat", json={"text": "The garden looks nice"})
    msgs = _last_chat_call(mock_llm)
    assert msgs[0]["role"] == "system"
    assert [m["content"] for m in msgs[1:]][0] == "I didn't sleep well"
    assert msgs[-1] == {"role": "user", "content": "The garden looks nice"}
    assert len(msgs) == 1 + 3  # system + user, assistant, user


def test_recent_symptoms_reach_the_system_prompt(client: TestClient, mock_llm) -> None:
    with client.app.state.sessionmaker() as s:
        s.add(
            SymptomLog(
                elder_id=1,
                canonical="joint_pain",
                label="right knee pain",
                raw_quote="my knee hurts",
                last_seen=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        s.commit()
    client.post("/api/chat", json={"text": "Hello"})
    assert "right knee pain" in _last_chat_call(mock_llm)[0]["content"]


def test_llm_failure_uses_fallback_and_still_saves(failing_client: TestClient) -> None:
    body = failing_client.post("/api/chat", json={"text": "我今天有点累"}).json()
    assert body["fallback"] is True
    assert "走神" in body["reply_text"]
    assert [m.role for m in _messages(failing_client)] == ["user", "assistant"]


def test_greet(client: TestClient) -> None:
    r = client.post("/api/chat/greet")
    assert r.status_code == 200
    body = r.json()
    assert body["user_text"] == ""
    assert body["reply_text"].startswith("Good morning, Maggie")  # mock greet line
    assert [m.role for m in _messages(client)] == ["assistant"]


def test_greet_fallback_is_personalized(failing_client: TestClient) -> None:
    body = failing_client.post("/api/chat/greet", json={}).json()
    assert body["fallback"] is True
    assert "Maggie" in body["reply_text"]


@pytest.mark.parametrize(
    "payload", [{"text": ""}, {"text": "   "}, {}, {"text": "hi", "extra": 1}, {"text": "x" * 2001}]
)
def test_invalid_chat_payload(client: TestClient, payload: dict) -> None:
    assert client.post("/api/chat", json=payload).status_code == 422


def test_unknown_elder_404(client: TestClient) -> None:
    assert client.post("/api/chat", json={"text": "hi", "elder_id": 99}).status_code == 404
    assert client.post("/api/chat/greet", json={"elder_id": 99}).status_code == 404


def test_reply_is_limited_to_one_question(settings: Settings) -> None:
    chatty = MockLLMClient({"chat": {"default": "Oh no. Is it your knee? Or your back?"}})
    with TestClient(create_app(settings, llm=chatty)) as c:
        body = c.post("/api/chat", json={"text": "I hurt"}).json()
        assert body["reply_text"] == "Oh no. Is it your knee?"
        assert [m.text for m in _messages(c)][-1] == "Oh no. Is it your knee?"  # saved trimmed
