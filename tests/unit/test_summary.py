from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.db import init_db, make_engine, make_sessionmaker
from elder_companion.llm import LLMError
from elder_companion.llm.mock import MockLLMClient
from elder_companion.models import Alert, Message, SymptomLog
from elder_companion.seed import seed_demo
from elder_companion.summary import EMPTY_SUMMARY, DailySummary

pytestmark = pytest.mark.unit

LA = ZoneInfo("America/Los_Angeles")
NOW_LOCAL = datetime(2026, 9, 29, 15, 0, tzinfo=LA)
NOW_UTC = datetime(2026, 9, 29, 22, 0)  # same instant, naive UTC


class Clock:
    def __init__(self) -> None:
        self.now = NOW_UTC

    def __call__(self) -> datetime:
        return self.now


class FlakyLLM(MockLLMClient):
    fail = True

    def extract_json(self, messages, *, schema, name):  # noqa: ANN001
        if self.fail:
            raise LLMError("down")
        return super().extract_json(messages, schema=schema, name=name)


@pytest.fixture
def session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with make_sessionmaker(engine)() as s:
        seed_demo(s)
        yield s


def _say(s: Session, text: str, at: datetime = NOW_UTC - timedelta(hours=1)) -> None:
    s.add(Message(elder_id=1, role="user", text=text, created_at=at))
    s.add(Message(elder_id=1, role="assistant", text="I see.", created_at=at))
    s.commit()


def _summary_calls(llm: MockLLMClient) -> list:
    """Since H7 the summary is a structured `daily_digest` call, not a plain chat."""
    return [c["messages"] for name, c in llm.calls if name == "extract_json"]


def test_no_chat_today_is_empty_and_free(session: Session) -> None:
    _say(session, "yesterday's chat", at=NOW_UTC - timedelta(days=1))
    llm = MockLLMClient.from_config()
    r = DailySummary(llm, companion_name="Hallo", clock=Clock()).get(session, 1, NOW_LOCAL)
    assert (r.summary, r.empty, r.fallback) == (EMPTY_SUMMARY, True, False)
    assert _summary_calls(llm) == []


def test_prompt_has_today_only_with_local_times(session: Session) -> None:
    _say(session, "yesterday's chat", at=NOW_UTC - timedelta(days=1))
    _say(session, "I watered the roses", at=datetime(2026, 9, 29, 16, 5))  # 9:05 AM in LA
    session.add(
        SymptomLog(
            elder_id=1,
            canonical="dizziness",
            label="morning dizziness",
            severity="mild",
            duration="2 days",
            raw_quote="头有点晕",
            count=2,
            first_seen=NOW_UTC - timedelta(hours=2),
            last_seen=NOW_UTC - timedelta(hours=2),
        )
    )
    session.add(
        Alert(elder_id=1, type="fall", level="high", title="Fall detected", created_at=NOW_UTC)
    )
    session.commit()
    llm = MockLLMClient.from_config()
    r = DailySummary(llm, companion_name="Hallo", clock=Clock()).get(session, 1, NOW_LOCAL)
    # An emergency alert leads the summary (daily_summary.txt), so the fall comes first.
    assert r.summary.startswith("A fall was detected") and not r.fallback

    system, user = _summary_calls(llm)[0]
    assert "Maggie" in system["content"] and "never mention medicines" in system["content"]
    assert "mood_score" in system["content"]
    notes = user["content"]
    assert "[9:05 AM] Elder: I watered the roses" in notes
    assert "Hallo: I see." in notes
    assert "yesterday's chat" not in notes
    assert (
        "dizziness: morning dizziness (severity mild, for 2 days, status new, mentioned 2x)"
        in notes
    )
    assert "high: Fall detected" in notes


def test_stored_digest_is_reused_until_the_day_changes(session: Session) -> None:
    _say(session, "hello")
    llm, clock = MockLLMClient.from_config(), Clock()
    summary = DailySummary(llm, companion_name="Hallo", clock=clock)
    first = summary.get(session, 1, NOW_LOCAL)
    clock.now += timedelta(hours=3)  # time alone never invalidates it
    assert summary.get(session, 1, NOW_LOCAL) == first
    assert len(_summary_calls(llm)) == 1

    _say(session, "my knee hurts")  # new message -> regenerate right away, no TTL to wait out
    assert summary.get(session, 1, NOW_LOCAL) != first
    assert len(_summary_calls(llm)) == 2


def test_refresh_regenerates(session: Session) -> None:
    _say(session, "hello")
    llm = MockLLMClient.from_config()
    summary = DailySummary(llm, companion_name="Hallo", clock=Clock())
    summary.get(session, 1, NOW_LOCAL)
    summary.get(session, 1, NOW_LOCAL, refresh=True)
    assert len(_summary_calls(llm)) == 2


def test_a_new_privacy_mark_regenerates_the_day(session: Session) -> None:
    """The request usually arrives after the content it covers, so the digest must move."""
    _say(session, "My friend Linda got bad news")
    llm = MockLLMClient.from_config()
    summary = DailySummary(llm, companion_name="Hallo", clock=Clock())
    summary.get(session, 1, NOW_LOCAL)
    for m in session.scalars(select(Message)):
        m.private = True
    session.commit()
    r = summary.get(session, 1, NOW_LOCAL)
    assert r.has_private and "Linda" not in r.summary


def test_llm_failure_gives_plain_summary_and_is_not_cached(session: Session) -> None:
    _say(session, "hello")
    session.add(
        Alert(elder_id=1, type="symptom", level="high", title="Chest pain", created_at=NOW_UTC)
    )
    session.commit()
    llm = FlakyLLM.from_config()
    summary = DailySummary(llm, companion_name="Hallo", clock=Clock())
    r = summary.get(session, 1, NOW_LOCAL)
    assert r.fallback
    assert r.summary == "Maggie chatted 1 time that day."
    llm.fail = False
    assert not summary.get(session, 1, NOW_LOCAL).fallback
