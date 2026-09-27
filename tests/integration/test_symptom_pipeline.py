"""D4 / M1: a chat turn logs symptoms and red flags reach the dashboard's SSE stream."""

import json
import socket
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from elder_companion.llm import LLMError
from elder_companion.llm.mock import MOCK_AUDIO_PREFIX, MockLLMClient
from elder_companion.models import Alert, Message, SymptomLog
from elder_companion.settings import Settings
from elder_companion.symptoms.extractor import SymptomExtractor
from elder_companion.symptoms.service import SymptomService, process_message_symptoms
from elder_companion.web.app import create_app

pytestmark = pytest.mark.integration

DIZZY = "这两天早上起来头有点晕"
CHEST = "胸口闷，喘不上气"


class FailingExtractLLM(MockLLMClient):
    def extract_json(self, messages, *, schema, name):  # noqa: ANN001
        raise LLMError("extraction timed out")


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient.from_config()


@pytest.fixture
def client(settings: Settings, mock_llm: MockLLMClient) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=mock_llm)) as c:
        yield c


def _symptoms(c: TestClient) -> list[SymptomLog]:
    with c.app.state.sessionmaker() as s:
        return list(s.scalars(select(SymptomLog).order_by(SymptomLog.id)))


def _alerts(c: TestClient) -> list[Alert]:
    with c.app.state.sessionmaker() as s:
        return list(s.scalars(select(Alert).order_by(Alert.id)))


def _extract_calls(llm: MockLLMClient) -> list[dict]:
    """Symptom / memory extraction calls (the family redaction pass is not extraction)."""
    return [
        args
        for name, args in llm.calls
        if name == "extract_json" and args["name"] != "family_redaction"
    ]


def test_dizziness_is_logged_without_alert(client: TestClient) -> None:
    r = client.post("/api/chat", json={"text": DIZZY})
    assert r.status_code == 200
    [row] = _symptoms(client)
    assert (row.canonical, row.severity, row.status, row.count) == ("dizziness", "mild", "new", 1)
    assert row.raw_quote == "头有点晕"
    with client.app.state.sessionmaker() as s:
        assert s.get(Message, row.message_id).text == DIZZY
    assert _alerts(client) == []  # mild, not a red flag


def test_red_flags_raise_high_alerts(client: TestClient) -> None:
    client.post("/api/chat", json={"text": CHEST})
    rows = _symptoms(client)
    assert [r.canonical for r in rows] == ["chest_pain", "shortness_of_breath"]
    alerts = _alerts(client)
    assert [(a.type, a.level, a.title) for a in alerts] == [
        ("symptom", "high", "Chest pain"),
        ("symptom", "high", "Shortness of breath"),
    ]
    assert [a.ref_id for a in alerts] == [str(r.id) for r in rows]
    assert CHEST in alerts[0].content and "Maggie" in alerts[0].content
    assert [a["title"] for a in client.get("/api/alerts").json()][:2] == [
        "Shortness of breath",
        "Chest pain",
    ]


def test_repeat_within_window_merges_and_debounces(client: TestClient) -> None:
    client.post("/api/chat", json={"text": CHEST})
    client.post("/api/chat", json={"text": "我还是" + CHEST})
    rows = _symptoms(client)
    assert [(r.canonical, r.count, r.status) for r in rows] == [
        ("chest_pain", 2, "ongoing"),
        ("shortness_of_breath", 2, "ongoing"),
    ]
    assert len(_alerts(client)) == 2  # no second round of alerts within 2h


def test_voice_turn_runs_the_pipeline(client: TestClient) -> None:
    audio = MOCK_AUDIO_PREFIX + DIZZY.encode()
    r = client.post("/api/chat/audio", files={"audio": ("blob", audio, "audio/webm")})
    assert r.status_code == 200 and r.json()["user_text"] == DIZZY
    assert [s.canonical for s in _symptoms(client)] == ["dizziness"]


def test_retry_and_greet_do_not_extract(client: TestClient, mock_llm: MockLLMClient) -> None:
    client.post("/api/chat/greet")
    r = client.post("/api/chat/audio", files={"audio": ("blob", b"MOCK:", "audio/webm")})
    assert r.json()["need_retry"] is True
    assert _extract_calls(mock_llm) == []


def test_extractor_sees_recent_turns(client: TestClient, mock_llm: MockLLMClient) -> None:
    client.post("/api/chat", json={"text": "I watered the roses today."})
    client.post("/api/chat", json={"text": "I'm a bit dizzy now"})
    user_msg = _extract_calls(mock_llm)[-1]["messages"][-1]["content"]
    assert "Elder: I watered the roses today." in user_msg
    assert "Companion: " in user_msg
    assert user_msg.endswith("I'm a bit dizzy now")
    assert user_msg.count("I'm a bit dizzy now") == 1  # the current turn is not in the context


def test_extraction_failure_does_not_affect_chat(settings: Settings) -> None:
    with TestClient(create_app(settings, llm=FailingExtractLLM.from_config())) as c:
        r = c.post("/api/chat", json={"text": CHEST})
        assert r.status_code == 200
        # CHEST is Chinese, so the reply is too, and it names the Chinese emergency
        # number. Assert on the behaviour — tell her to call for help now — not on "911".
        body = r.json()
        assert body["fallback"] is False
        assert "120" in body["reply_text"] or "911" in body["reply_text"]
        assert _symptoms(c) == [] and _alerts(c) == []


def test_pipeline_ignores_missing_and_assistant_messages(client: TestClient) -> None:
    client.post("/api/chat", json={"text": "hello"})
    state = client.app.state
    run = lambda mid: process_message_symptoms(  # noqa: E731
        state.sessionmaker, state.symptom_extractor, state.settings.symptoms, None, mid
    )
    assert run(2) == []  # the assistant reply
    assert run(999) == []


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 29, 8, 0)

    def __call__(self) -> datetime:
        return self.now


def _say(c: TestClient, clock: Clock, text: str) -> list[Alert]:
    with c.app.state.sessionmaker() as s:
        msg = Message(elder_id=1, role="user", text=text)
        s.add(msg)
        s.commit()
        service = SymptomService(
            s,
            SymptomExtractor(MockLLMClient.from_config()),
            c.app.state.settings.symptoms,
            now=clock,
        )
        return service.process_message(msg.id).alerts


def test_windows_follow_settings(client: TestClient) -> None:
    clock = Clock()
    assert len(_say(client, clock, CHEST)) == 2

    clock.now += timedelta(hours=1)
    assert _say(client, clock, CHEST) == []  # debounced

    clock.now += timedelta(hours=2)  # 3h after the first alert
    assert len(_say(client, clock, CHEST)) == 2  # debounce expired, still the same rows
    assert [r.count for r in _symptoms(client)] == [3, 3]

    clock.now += timedelta(hours=25)  # past the 24h merge window
    _say(client, clock, CHEST)
    assert [r.count for r in _symptoms(client)] == [3, 3, 1, 1]


# --- SSE over a real server (TestClient cannot consume an endless stream) ---


@pytest.fixture
def live(settings: Settings) -> Iterator[tuple[str, FastAPI]]:
    app = create_app(settings, llm=MockLLMClient.from_config())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}", app
    server.should_exit = True
    thread.join(timeout=10)


def _read_events(lines: Iterator[str], until: str) -> list[tuple[str, dict]]:
    """Collect (event, data) pairs up to and including the first `until` event."""
    events, event = [], None
    for line in lines:
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:") and event:
            events.append((event, json.loads(line.removeprefix("data:"))))
            if event == until:
                return events
    return events


def test_sse_streams_alerts_and_activity(live: tuple[str, FastAPI]) -> None:
    url, app = live
    with httpx.Client(base_url=url, timeout=10) as http:
        with http.stream("GET", "/api/alerts/stream") as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            # Headers can arrive before the stream's generator has subscribed.
            deadline = time.monotonic() + 5
            while app.state.alert_bus.subscriber_count == 0:
                assert time.monotonic() < deadline, "SSE stream never subscribed"
                time.sleep(0.02)
            lines = stream.iter_lines()

            http.post("/api/chat", json={"text": CHEST}).raise_for_status()
            turn = _read_events(lines, until="activity")

            fall = {"type": "fall", "level": "high", "title": "Fall detected"}
            http.post("/api/alerts", json=fall).raise_for_status()
            [(event, data)] = _read_events(lines, until="alert")

    assert [(e, d.get("title")) for e, d in turn] == [
        ("alert", "Chest pain"),
        ("alert", "Shortness of breath"),
        ("activity", None),
    ]
    assert all(d["level"] == "high" and d["type"] == "symptom" for _, d in turn[:2])
    assert turn[2][1] == {"message_id": 1, "symptoms": 2}
    assert (event, data["type"], data["title"]) == ("alert", "fall", "Fall detected")
