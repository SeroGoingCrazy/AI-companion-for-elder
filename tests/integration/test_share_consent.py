"""Ask before sharing (consent.py, mock LLM): symptoms and personal matters stay off the
family's views until she agrees; each subject is asked about once; red flags never wait."""

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from elder_companion.alerts.bus import StreamEvent
from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import Message, ShareConsent
from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.integration

SEVERE_KNEE = {
    "match": ["terrible knee"],
    "result": {
        "symptoms": [
            {
                "canonical": "joint_pain",
                "label": "knee pain",
                "body_part": "knee",
                "severity": "severe",
                "duration": None,
                "onset": None,
                "status": "new",
                "raw_quote": "terrible knee",
            }
        ]
    },
}


@pytest.fixture
def mock_llm() -> MockLLMClient:
    cfg = MockLLMClient.from_config().config
    cfg["json"]["symptom_extraction"]["rules"].insert(0, SEVERE_KNEE)
    return MockLLMClient(cfg)


@pytest.fixture
def client(settings: Settings, mock_llm: MockLLMClient) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=mock_llm)) as c:
        yield c


@pytest.fixture
def published(client: TestClient) -> list:
    items: list = []
    client.app.state.alert_bus.publish = lambda item: items.append(item) or 1
    return items


def _symptoms(c: TestClient) -> list[str]:
    days = c.get("/api/symptoms?days=1").json()["days"]
    return [s["canonical"] for d in days for s in d["symptoms"]]


def _say(c: TestClient, text: str) -> dict:
    return c.post("/api/chat", json={"text": text}).json()


def _consents(c: TestClient) -> dict[str, str]:
    with c.app.state.sessionmaker() as s:
        return {r.key: r.decision for r in s.scalars(select(ShareConsent))}


def test_symptom_waits_for_yes(client: TestClient) -> None:
    r = _say(client, "My knee hurts again.")
    assert "joint_pain" not in _symptoms(client)
    msgs = client.get("/api/messages").json()
    assert [m["awaiting_consent"] for m in msgs[-2:]] == [True, True]
    assert "knee" not in client.get("/api/messages").text
    assert _consents(client) == {"symptom:joint_pain": "pending"}
    with client.app.state.sessionmaker() as s:
        assert s.scalars(select(ShareConsent)).one().asked_message_id == r["message_id"]

    _say(client, "Yes, you can tell her.")
    assert "joint_pain" in _symptoms(client)
    assert "My knee hurts again." in client.get("/api/messages").text
    assert _consents(client) == {"symptom:joint_pain": "share"}


def test_asked_once_then_remembered(client: TestClient, mock_llm: MockLLMClient) -> None:
    _say(client, "My knee hurts again.")
    _say(client, "Yes, you can tell her.")
    _say(client, "My knee is sore today too.")  # already agreed: shown right away
    assert "My knee is sore today too." in client.get("/api/messages").text
    system = [m for name, m in mock_llm.calls if name == "chat"][-1][0]["content"]
    assert "- joint pain: she is happy for Amy to know; don't ask again" in system


def test_no_keeps_it_hidden_for_good(client: TestClient, mock_llm: MockLLMClient) -> None:
    _say(client, "My knee hurts again.")
    _say(client, "No, don't tell them.")
    _say(client, "My knee is sore today too.")
    assert "joint_pain" not in _symptoms(client)
    text = client.get("/api/messages").text
    assert "knee" not in text and "don't tell" not in text
    msgs = client.get("/api/messages").json()
    assert all(m["private"] and not m["awaiting_consent"] for m in msgs)
    assert _consents(client) == {"symptom:joint_pain": "private"}
    system = [m for name, m in mock_llm.calls if name == "chat"][-1][0]["content"]
    assert "- joint pain: she wants this kept between you" in system


def test_personal_topic_waits_for_yes(client: TestClient) -> None:
    _say(client, "I'm worried about money this month.")
    assert "money" not in client.get("/api/messages").text
    assert _consents(client) == {"topic:money worries": "pending"}
    _say(client, "Yes, please tell her.")
    assert "worried about money" in client.get("/api/messages").text


def test_red_flags_never_wait(client: TestClient, published: list) -> None:
    _say(client, "My chest feels tight and I can't catch my breath")
    assert {"chest_pain", "shortness_of_breath"} <= set(_symptoms(client))
    assert "chest feels tight" in client.get("/api/messages").text
    assert _consents(client) == {}
    alerts = [e for e in published if isinstance(e, StreamEvent) and e.event == "alert"]
    assert {json.loads(a.data)["level"] for a in alerts} == {"high"}


def test_non_urgent_alert_is_pushed_only_after_yes(client: TestClient, published: list) -> None:
    _say(client, "I have a terrible knee ache.")
    assert not [e for e in published if e.event == "alert"]
    assert client.get("/api/alerts").json() == []
    _say(client, "Yes, you can tell her.")
    [alert] = client.get("/api/alerts").json()
    assert alert["level"] == "medium"


def test_keep_between_us_is_never_released(client: TestClient) -> None:
    _say(client, "My knee hurts, keep this between us.")
    with client.app.state.sessionmaker() as s:
        assert all(m.consent_keys is None for m in s.scalars(select(Message)))
    _say(client, "Yes, you can tell her.")
    assert "keep this between us" not in client.get("/api/messages").text


def test_switched_off_shares_as_before(
    settings: Settings, mock_llm: MockLLMClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    off = settings.model_copy(
        update={"privacy": settings.privacy.model_copy(update={"ask_before_sharing": False})}
    )
    with TestClient(create_app(off, llm=mock_llm)) as c:
        _say(c, "My knee hurts again.")
        assert "joint_pain" in _symptoms(c)
