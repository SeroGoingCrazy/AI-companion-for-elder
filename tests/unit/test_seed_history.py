from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from elder_companion.chat.context import build_greet_context
from elder_companion.dashboard import symptom_timeline
from elder_companion.db import init_db, make_engine, make_sessionmaker
from elder_companion.models import Alert, Elder, Message, SymptomLog
from elder_companion.seed import seed_demo, seed_history
from elder_companion.symptoms.schema import get_catalog

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


@pytest.fixture
def session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with make_sessionmaker(engine)() as s:
        seed_demo(s)
        yield s


def test_history_fills_the_past_six_days(session: Session) -> None:
    assert seed_history(session, NOW)
    days = symptom_timeline(session, 1, NOW, 7)
    assert days[0].label == "Today" and days[0].symptoms == []  # today is left for the demo
    assert all(d.symptoms for d in days[1:])
    assert [s.canonical for s in days[1].symptoms] == ["joint_pain"]  # yesterday: knee again
    assert all(s.canonical in get_catalog() for d in days for s in d.symptoms)


def test_messages_alternate_and_are_chronological(session: Session) -> None:
    seed_history(session, NOW)
    msgs = list(session.scalars(select(Message).order_by(Message.id)))
    assert [m.role for m in msgs[:2]] == ["user", "assistant"]
    assert all(a.created_at < b.created_at for a, b in zip(msgs, msgs[1:], strict=False))


def test_one_past_alert_linked_to_its_symptom(session: Session) -> None:
    seed_history(session, NOW)
    [alert] = session.scalars(select(Alert))
    assert (alert.level, alert.title, alert.is_read) == ("medium", "Joint pain", True)
    assert session.get(SymptomLog, int(alert.ref_id)).severity == "severe"


def test_is_idempotent(session: Session) -> None:
    seed_history(session, NOW)
    count = session.scalar(select(func.count(Message.id)))
    assert seed_history(session, NOW) is False
    assert session.scalar(select(func.count(Message.id))) == count


def test_greeting_follows_up_on_yesterdays_knee(session: Session) -> None:
    seed_history(session, NOW)
    since = NOW.astimezone(ZoneInfo("UTC")).replace(tzinfo=None) - timedelta(hours=48)
    recent = list(
        session.scalars(
            select(SymptomLog)
            .where(SymptomLog.last_seen >= since, SymptomLog.status != "resolved")
            .order_by(SymptomLog.last_seen.desc())
        )
    )
    assert (recent[0].label, recent[0].status) == ("knee pain", "ongoing")
    elder = session.get(Elder, 1)
    msgs = build_greet_context(elder, [], recent, NOW, companion_name="Sunny", history_turns=10)
    assert "ask how her knee pain is doing" in msgs[-1]["content"]
