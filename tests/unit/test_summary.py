from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
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

    def chat(self, messages, *, max_tokens=None):  # noqa: ANN001
        if self.fail:
            raise LLMError("down")
        return super().chat(messages, max_tokens=max_tokens)


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
    return [m for name, m in llm.calls if name == "chat"]


def test_no_chat_today_is_empty_and_free(session: Session) -> None:
    _say(session, "yesterday's chat", at=NOW_UTC - timedelta(days=1))
    llm = MockLLMClient.from_config()
    r = DailySummary(llm, companion_name="Sunny", clock=Clock()).get(session, 1, NOW_LOCAL)
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
    r = DailySummary(llm, companion_name="Sunny", clock=Clock()).get(session, 1, NOW_LOCAL)
    assert r.summary.startswith("Maggie") and not r.fallback

    system, user = _summary_calls(llm)[0]
    assert "Maggie" in system["content"] and "never mention medicines" in system["content"]
    notes = user["content"]
    assert "[9:05 AM] Elder: I watered the roses" in notes
    assert "Sunny: I see." in notes
    assert "yesterday's chat" not in notes
    assert (
        "dizziness: morning dizziness (severity mild, for 2 days, status new, mentioned 2x)"
        in notes
    )
    assert "high: Fall detected" in notes


def test_cache_reused_until_data_changes(session: Session) -> None:
    _say(session, "hello")
    llm, clock = MockLLMClient.from_config(), Clock()
    summary = DailySummary(llm, companion_name="Sunny", clock=clock)
    first = summary.get(session, 1, NOW_LOCAL)
    clock.now += timedelta(minutes=5)
    assert summary.get(session, 1, NOW_LOCAL) is first
    assert len(_summary_calls(llm)) == 1

    _say(session, "my knee hurts")  # new message -> regenerate right away
    second = summary.get(session, 1, NOW_LOCAL)
    assert second is not first and len(_summary_calls(llm)) == 2


def test_cache_expires_after_ttl_and_on_refresh(session: Session) -> None:
    _say(session, "hello")
    llm, clock = MockLLMClient.from_config(), Clock()
    summary = DailySummary(llm, companion_name="Sunny", clock=clock, ttl=timedelta(minutes=10))
    summary.get(session, 1, NOW_LOCAL)
    summary.get(session, 1, NOW_LOCAL, refresh=True)
    assert len(_summary_calls(llm)) == 2
    clock.now += timedelta(minutes=11)
    summary.get(session, 1, NOW_LOCAL)
    assert len(_summary_calls(llm)) == 3


def test_llm_failure_gives_plain_summary_and_is_not_cached(session: Session) -> None:
    _say(session, "hello")
    session.add(
        Alert(elder_id=1, type="symptom", level="high", title="Chest pain", created_at=NOW_UTC)
    )
    session.commit()
    llm = FlakyLLM.from_config()
    summary = DailySummary(llm, companion_name="Sunny", clock=Clock())
    r = summary.get(session, 1, NOW_LOCAL)
    assert r.fallback
    assert r.summary == "Maggie chatted 1 time today. Alerts: Chest pain (high)."
    llm.fail = False
    assert not summary.get(session, 1, NOW_LOCAL).fallback
