"""H5: the reminder loop end to end (mock LLM).

Amy sets a reminder -> the next greeting raises it in her words -> Maggie answers ->
the dashboard shows today confirmed.
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import Reminder, ReminderLog
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


@pytest.fixture
def today(settings: Settings) -> date:
    return datetime.now(settings.chat.tz).date()


def _greet_instruction(llm: MockLLMClient) -> str:
    return [args for name, args in llm.calls if name == "chat"][-1][-1]["content"]


def _deactivate_seeded(c: TestClient) -> None:
    """Drop the seeded 08:00 pill so each test controls exactly which reminders are due."""
    with c.app.state.sessionmaker() as s:
        for r in s.scalars(select(Reminder)):
            r.active = False
        s.commit()


def _add(c: TestClient, text: str, **kw: object) -> dict:
    r = c.post("/api/reminders", json={"text": text, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- the seeded demo reminder ----------


def test_the_demo_starts_with_amys_pill_reminder(client: TestClient) -> None:
    [pill] = client.get("/api/reminders").json()
    assert pill["text"] == "blood pressure pill with breakfast"
    assert pill["schedule_time"] == "08:00" and pill["from_member_name"] == "Amy"
    assert len(pill["history"]) == 7


def test_seeded_history_fills_in_adherence(
    settings: Settings, client: TestClient, today: date
) -> None:
    with client.app.state.sessionmaker() as s:
        seed_history(s, datetime.now(settings.chat.tz))
    [pill] = client.get("/api/reminders").json()
    assert pill["confirmed_days"] == 4 and pill["raised_days"] == 6
    statuses = {h["date"]: h["status"] for h in pill["history"]}
    assert statuses[str(today - timedelta(days=4))] == "no_response"
    assert statuses[str(today)] is None  # not raised yet today


# ---------- creating and deactivating ----------


def test_create_a_daily_reminder(client: TestClient) -> None:
    created = _add(client, "  water the orchid  ", schedule_time="7:5")
    assert created["text"] == "water the orchid"  # trimmed
    assert created["schedule_time"] == "07:05"  # normalized
    assert created["today_status"] is None and created["active"]


def test_create_a_one_off_reminder(client: TestClient, today: date) -> None:
    created = _add(client, "ask if she booked the eye doctor", schedule_date=str(today))
    assert created["schedule_date"] == str(today) and created["schedule_time"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"text": "x"},  # neither schedule
        {"text": "x", "schedule_time": "08:00", "schedule_date": "2026-09-26"},  # both
        {"text": "   ", "schedule_time": "08:00"},  # blank
        {"text": "x", "schedule_time": "25:00"},  # not a time
    ],
)
def test_bad_reminders_are_rejected(client: TestClient, body: dict) -> None:
    assert client.post("/api/reminders", json=body).status_code == 422


def test_unknown_family_member_is_a_404(client: TestClient) -> None:
    r = client.post(
        "/api/reminders", json={"text": "x", "schedule_time": "08:00", "from_member_id": 999}
    )
    assert r.status_code == 404


def test_deactivating_keeps_the_history(client: TestClient, today: date) -> None:
    _deactivate_seeded(client)
    created = _add(client, "pill", schedule_date=str(today))
    client.post("/api/chat/greet")  # raises it, writing one log row
    gone = client.delete(f"/api/reminders/{created['id']}").json()
    assert gone["active"] is False and gone["raised_days"] == 1
    listed = {r["id"] for r in client.get("/api/reminders").json()}
    assert created["id"] in listed  # deactivated, but still shown with its history


def test_deactivating_an_unknown_reminder_is_a_404(client: TestClient) -> None:
    assert client.delete("/api/reminders/999").status_code == 404


# ---------- the greeting raises it ----------


def test_greeting_raises_a_due_reminder_in_the_familys_words(
    client: TestClient, llm: MockLLMClient, today: date
) -> None:
    _deactivate_seeded(client)
    created = _add(client, "blood pressure pill", schedule_date=str(today), from_member_id=1)
    client.post("/api/chat/greet")
    instruction = _greet_instruction(llm)
    assert 'Amy asked you to check on this: "blood pressure pill"' in instruction
    assert "no dose" in instruction  # the wording guardrail travels with it
    assert client.get("/api/reminders").json()[0]["today_status"] == "mentioned"
    with client.app.state.sessionmaker() as s:
        log = s.scalars(select(ReminderLog)).one()
        assert log.reminder_id == created["id"] and log.date == today


def test_a_reminder_is_raised_once_a_day(client: TestClient, today: date) -> None:
    _deactivate_seeded(client)
    _add(client, "pill", schedule_date=str(today))
    client.post("/api/chat/greet")
    client.post("/api/chat/greet")
    with client.app.state.sessionmaker() as s:
        assert len(list(s.scalars(select(ReminderLog)))) == 1


def test_an_undue_reminder_is_not_raised(client: TestClient, today: date) -> None:
    _deactivate_seeded(client)
    _add(client, "pill", schedule_date=str(today + timedelta(days=3)))
    client.post("/api/chat/greet")
    with client.app.state.sessionmaker() as s:
        assert list(s.scalars(select(ReminderLog))) == []


def test_a_reminder_set_mid_session_waits_for_the_next_greeting(
    client: TestClient, today: date
) -> None:
    """Spec 3.8: delivery happens at the greeting, so status stays deterministic."""
    _deactivate_seeded(client)
    client.post("/api/chat/greet")
    _add(client, "pill", schedule_date=str(today))
    client.post("/api/chat", json={"text": "It's a nice day"})
    assert client.get("/api/reminders").json()[0]["today_status"] is None
    client.post("/api/chat/greet")
    assert client.get("/api/reminders").json()[0]["today_status"] == "mentioned"


# ---------- she answers ----------


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("Yes, I took it with breakfast", "confirmed"),
        ("吃过了", "confirmed"),
        ("Not yet, after lunch", "declined"),
        ("还没呢", "declined"),
    ],
)
def test_her_answer_lands_on_the_dashboard(
    client: TestClient, today: date, answer: str, expected: str
) -> None:
    _deactivate_seeded(client)
    _add(client, "blood pressure pill", schedule_date=str(today))
    client.post("/api/chat/greet")
    client.post("/api/chat", json={"text": answer})
    assert client.get("/api/reminders").json()[0]["today_status"] == expected


def test_an_unrelated_answer_leaves_it_open(client: TestClient, today: date) -> None:
    _deactivate_seeded(client)
    _add(client, "pill", schedule_date=str(today))
    client.post("/api/chat/greet")
    client.post("/api/chat", json={"text": "The garden is looking lovely"})
    assert client.get("/api/reminders").json()[0]["today_status"] == "mentioned"


def test_the_first_answer_of_the_day_wins(client: TestClient, today: date) -> None:
    """She says she took it, then rambles about taking it again: adherence must not flip."""
    _deactivate_seeded(client)
    _add(client, "pill", schedule_date=str(today))
    client.post("/api/chat/greet")
    client.post("/api/chat", json={"text": "Yes, I took it"})
    client.post("/api/chat", json={"text": "Not yet actually"})
    assert client.get("/api/reminders").json()[0]["today_status"] == "confirmed"


def test_an_answer_with_nothing_raised_is_ignored(client: TestClient) -> None:
    """The model must not be able to invent an ack for a reminder she was never asked."""
    _deactivate_seeded(client)
    client.post("/api/chat", json={"text": "Yes, I took it"})
    with client.app.state.sessionmaker() as s:
        assert list(s.scalars(select(ReminderLog))) == []


def test_yesterdays_unanswered_reminder_becomes_no_response(
    client: TestClient, today: date
) -> None:
    _deactivate_seeded(client)
    created = _add(client, "pill", schedule_time="00:00")
    with client.app.state.sessionmaker() as s:
        s.add(
            ReminderLog(
                reminder_id=created["id"], date=today - timedelta(days=1), status="mentioned"
            )
        )
        s.commit()
    client.post("/api/chat/greet")  # the greeting sweeps closed days first
    statuses = {h["date"]: h["status"] for h in client.get("/api/reminders").json()[0]["history"]}
    assert statuses[str(today - timedelta(days=1))] == "no_response"
    assert statuses[str(today)] == "mentioned"
