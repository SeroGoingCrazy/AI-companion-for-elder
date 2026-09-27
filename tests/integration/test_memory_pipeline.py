"""H2/H4: a chat turn feeds companion memory; privacy requests mark the segment while red
flags still alert (mock LLM)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from elder_companion.llm import LLMError
from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import Alert, MemoryItem, Message, SymptomLog
from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.integration


class FailingMemoryLLM(MockLLMClient):
    def extract_json(self, messages, *, schema, name):  # noqa: ANN001
        if name == "memory_extraction":
            raise LLMError("memory extraction timed out")
        return super().extract_json(messages, schema=schema, name=name)


def _client(settings: Settings, llm: MockLLMClient) -> TestClient:
    return TestClient(create_app(settings, llm=llm))


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with _client(settings, MockLLMClient.from_config()) as c:
        yield c


def _rows(c: TestClient, model):  # noqa: ANN001, ANN202
    with c.app.state.sessionmaker() as s:
        return list(s.scalars(select(model).order_by(model.id)))


def test_plan_becomes_an_open_follow_up(client: TestClient) -> None:
    client.post("/api/chat", json={"text": "I'm going to repot my orchid this week"})
    [item] = _rows(client, MemoryItem)
    assert (item.kind, item.subject, item.status, item.private) == (
        "follow_up",
        "orchid",
        "open",
        False,
    )
    assert item.due_date is not None


def test_privacy_request_hides_the_turn_and_the_reply(client: TestClient) -> None:
    client.post("/api/chat", json={"text": "I watered the roses"})
    client.post(
        "/api/chat",
        json={"text": "My friend Linda got bad news from her doctor. Keep this between us, okay?"},
    )
    msgs = _rows(client, Message)
    assert [m.private for m in msgs] == [False, False, True, True]
    [linda] = _rows(client, MemoryItem)
    assert linda.subject == "Linda" and linda.private


def test_red_flag_inside_a_private_segment_still_alerts(client: TestClient) -> None:
    client.post("/api/chat", json={"text": "别告诉我女儿，我昨天在厨房摔了一跤"})
    assert _rows(client, Message)[0].private
    [fall] = [s for s in _rows(client, SymptomLog) if s.canonical == "fall"]
    [alert] = _rows(client, Alert)
    assert alert.level == "high" and "摔" in alert.content
    day = client.get("/api/symptoms?days=1").json()["days"][0]
    assert [s["canonical"] for s in day["symptoms"]] == ["fall"]
    assert client.get("/api/alerts").json()[0]["id"] == alert.id
    assert fall.raw_quote == "摔"


def test_memory_failure_does_not_affect_chat_or_symptoms(settings: Settings) -> None:
    with _client(settings, FailingMemoryLLM.from_config()) as c:
        r = c.post("/api/chat", json={"text": "I'm a bit dizzy, and I'll repot my orchid"})
        assert r.status_code == 200 and r.json()["reply_text"]
        assert [s.canonical for s in _rows(c, SymptomLog)] == ["dizziness"]
        assert _rows(c, MemoryItem) == []
