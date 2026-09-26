from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from elder_companion.chat.context import (
    NO_FOLLOW_UPS,
    build_context,
    build_greet_context,
    detect_language,
    format_now,
    part_of_day,
    relative_day,
)
from elder_companion.models import Elder, Message, SymptomLog
from elder_companion.seed import DEMO_ELDER

pytestmark = pytest.mark.unit

TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 9, 29, 8, 5, tzinfo=TZ)  # Tuesday morning, local
UTC_NOW = NOW.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _elder() -> Elder:
    return Elder(**DEMO_ELDER)


def _symptom(label: str, *, days_ago: float = 1, **kw) -> SymptomLog:
    defaults = dict(
        canonical="joint_pain",
        body_part=None,
        severity="unknown",
        status="ongoing",
        count=1,
        raw_quote=label,
    )
    return SymptomLog(
        label=label, last_seen=UTC_NOW - timedelta(days=days_ago), **{**defaults, **kw}
    )


def _msgs(n: int) -> list[Message]:
    return [Message(role="user" if i % 2 == 0 else "assistant", text=f"m{i}") for i in range(n)]


def _ctx(history=(), symptoms=(), turns=10):
    return build_context(
        _elder(), list(history), list(symptoms), NOW, companion_name="Sunny", history_turns=turns
    )


def test_system_prompt_has_persona_profile_and_time() -> None:
    system = _ctx()[0]
    assert system["role"] == "system"
    text = system["content"]
    assert "You are Sunny" in text
    assert 'Call her "Maggie"' in text
    assert "Margaret Lee" in text
    assert "High blood pressure" in text  # profile included
    assert "Tuesday, September 29, 2026, 8:05 AM" in text
    assert "(morning)" in text
    assert "$" not in text  # every placeholder filled


def test_system_prompt_has_safety_rules() -> None:
    text = _ctx()[0]["content"]
    assert "Never diagnose" in text
    assert "911" in text


def test_follow_ups_listed_with_details() -> None:
    s = _symptom("right knee pain", body_part="right knee", severity="mild", count=3)
    text = _ctx(symptoms=[s])[0]["content"]
    assert "- right knee pain (right knee, mild), last mentioned yesterday, 3 times so far" in text


def test_improved_noted_and_resolved_excluded() -> None:
    improved = _symptom("insomnia", status="improved")
    resolved = _symptom("cough", status="resolved")
    text = _ctx(symptoms=[improved, resolved])[0]["content"]
    assert "insomnia" in text and "getting better" in text
    assert "cough" not in text


def test_no_follow_ups_placeholder() -> None:
    assert NO_FOLLOW_UPS in _ctx()[0]["content"]


def test_history_truncated_to_turns_oldest_first() -> None:
    ctx = _ctx(history=_msgs(25), turns=10)
    body = ctx[1:]
    assert len(body) == 20
    assert [m["content"] for m in body] == [f"m{i}" for i in range(5, 25)]
    assert body[-1] == {"role": "user", "content": "m24"}


def test_zero_turns_keeps_no_history() -> None:
    assert len(_ctx(history=_msgs(4), turns=0)) == 1


def test_greet_context_ends_with_instruction() -> None:
    ctx = build_greet_context(
        _elder(),
        [],
        [_symptom("knee pain")],
        NOW,
        companion_name="Sunny",
        history_turns=10,
    )
    last = ctx[-1]
    assert last["role"] == "system"
    assert "morning" in last["content"]
    assert "English" in last["content"]
    assert "ask how her knee pain is doing" in last["content"]


def test_greet_language_follows_last_user_message() -> None:
    history = [Message(role="user", text="今天挺好的"), Message(role="assistant", text="太好了")]
    ctx = build_greet_context(_elder(), history, [], NOW, companion_name="Sunny", history_turns=10)
    assert "Chinese" in ctx[-1]["content"]
    assert "Ask how she is feeling today." in ctx[-1]["content"]


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (4, "night"),
        (5, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (17, "evening"),
        (22, "night"),
    ],
)
def test_part_of_day(hour: int, expected: str) -> None:
    assert part_of_day(NOW.replace(hour=hour)) == expected


def test_relative_day_and_format_now() -> None:
    assert relative_day(NOW, NOW) == "today"
    assert relative_day(NOW - timedelta(days=1), NOW) == "yesterday"
    assert relative_day(NOW - timedelta(days=3), NOW) == "3 days ago"
    assert format_now(NOW.replace(hour=0, minute=7)) == "Tuesday, September 29, 2026, 12:07 AM"


def test_relative_day_uses_local_date() -> None:
    # 11pm local yesterday is still "yesterday" even though it's the same UTC date as now.
    s = _symptom("headache", days_ago=0)
    s.last_seen = datetime(2026, 9, 29, 6, 0)  # 06:00 UTC = 23:00 PDT on Sep 28
    assert "last mentioned yesterday" in _ctx(symptoms=[s])[0]["content"]


def test_detect_language() -> None:
    assert detect_language("hello") == "en"
    assert detect_language("hello 你好") == "zh"
