"""H4: privacy span clamping, mark-on-write, and the visible_* read layer."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from elder_companion.db import init_db, make_engine, make_sessionmaker
from elder_companion.models import Alert, MemoryItem, Message, SymptomLog, SymptomMention
from elder_companion.privacy import (
    history_page,
    mark_private,
    privacy_span,
    visible_alerts,
    visible_memory,
    visible_messages,
    visible_symptoms,
)
from elder_companion.seed import seed_demo

pytestmark = pytest.mark.unit

T0 = datetime(2026, 9, 29, 16, 0)  # naive UTC


@pytest.fixture
def session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    with make_sessionmaker(engine)() as s:
        seed_demo(s)
        yield s


def _msg(s: Session, text: str, minutes: int, role: str = "user") -> Message:
    m = Message(elder_id=1, role=role, text=text, created_at=T0 + timedelta(minutes=minutes))
    s.add(m)
    s.flush()
    return m


def _symptom(s: Session, canonical: str, mentions: list[tuple[Message, str]]) -> SymptomLog:
    row = SymptomLog(
        elder_id=1,
        canonical=canonical,
        label=canonical,
        severity="mild",
        raw_quote=mentions[-1][1],
        count=len(mentions),
        message_id=mentions[-1][0].id,
        first_seen=mentions[0][0].created_at,
        last_seen=mentions[-1][0].created_at,
    )
    s.add(row)
    s.flush()
    for m, quote in mentions:
        s.add(
            SymptomMention(
                symptom_log_id=row.id,
                message_id=m.id,
                raw_quote=quote,
                severity="mild",
                created_at=m.created_at,
            )
        )
    s.flush()
    return row


# ---------- span ----------


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ([40, 42], [40, 42]),  # model's choice, within the span
        ([], [41, 42]),  # empty -> current + previous user message
        ([10, 42], [42]),  # ids outside the session/span are ignored
        ([30, 35, 40, 41], [35, 40, 41, 42]),  # at most 3 before the current one; current always
        ([99], [41, 42]),  # nothing valid -> default
    ],
)
def test_privacy_span_is_clamped(requested: list[int], expected: list[int]) -> None:
    assert privacy_span(42, [30, 35, 40, 41, 42], requested, max_span=3) == expected


def test_span_for_the_first_message_of_a_session() -> None:
    assert privacy_span(5, [5], [], max_span=3) == [5]


# ---------- messages ----------


def test_private_messages_are_redacted_in_history_and_left_out_of_visible(
    session: Session,
) -> None:
    a = _msg(session, "I watered the roses", 0)
    b = _msg(session, "My friend Linda got bad news", 1)
    c = _msg(session, "I'm so sorry about Linda", 2, role="assistant")
    assert mark_private(session, [b.id, c.id]) == 2
    page = history_page(session, 1, 10)
    assert [(m.id, m.text, m.private) for m in page] == [
        (a.id, "I watered the roses", False),
        (b.id, "", True),
        (c.id, "", True),
    ]
    visible, private_ids = visible_messages(session, 1, T0, T0 + timedelta(hours=1))
    assert [m.id for m in visible] == [a.id] and private_ids == (b.id, c.id)
    assert mark_private(session, [b.id]) == 0  # already private


# ---------- symptoms ----------


def test_symptom_with_only_private_mentions_is_hidden(session: Session) -> None:
    m = _msg(session, "keep it quiet but I'm dizzy", 0)
    _symptom(session, "dizziness", [(m, "dizzy")])
    mark_private(session, [m.id])
    assert visible_symptoms(session, 1) == []


def test_mixed_symptom_counts_and_quotes_visible_mentions_only(session: Session) -> None:
    m1 = _msg(session, "a bit dizzy this morning", 0)
    m2 = _msg(session, "really dizzy, don't tell Amy", 30)
    _symptom(session, "dizziness", [(m1, "a bit dizzy"), (m2, "really dizzy")])
    mark_private(session, [m2.id])
    [v] = visible_symptoms(session, 1)
    assert (v.count, v.raw_quote, v.last_seen) == (1, "a bit dizzy", m1.created_at)
    assert [x.raw_quote for x in v.mentions] == ["a bit dizzy"]


def test_red_flag_in_private_segment_stays_visible_with_the_quote(session: Session) -> None:
    m = _msg(session, "Don't tell my daughter, I fell in the kitchen", 0)
    _symptom(session, "fall", [(m, "I fell in the kitchen")])
    mark_private(session, [m.id])
    [v] = visible_symptoms(session, 1)
    assert v.canonical == "fall" and v.raw_quote == "I fell in the kitchen" and v.red_flag
    assert visible_symptoms(session, 1, bypass_levels=()) == []  # the bypass is the only reason


def test_rows_without_mentions_stay_visible(session: Session) -> None:
    session.add(
        SymptomLog(
            elder_id=1,
            canonical="cough",
            label="cough",
            raw_quote="cough",
            first_seen=T0,
            last_seen=T0,
        )
    )
    session.flush()
    assert [v.canonical for v in visible_symptoms(session, 1)] == ["cough"]


def test_time_bounds_use_the_visible_last_mention(session: Session) -> None:
    m1 = _msg(session, "dizzy", 0)
    m2 = _msg(session, "dizzy again", 120)
    _symptom(session, "dizziness", [(m1, "dizzy"), (m2, "dizzy again")])
    mark_private(session, [m2.id])
    assert visible_symptoms(session, 1, since=T0 + timedelta(minutes=60)) == []
    assert len(visible_symptoms(session, 1, until=T0 + timedelta(minutes=60))) == 1


# ---------- alerts ----------


def test_only_non_bypass_symptom_alerts_from_private_messages_are_hidden(
    session: Session,
) -> None:
    m = _msg(session, "private", 0)
    kw = {"elder_id": 1, "title": "t", "created_at": T0}
    session.add_all(
        [
            Alert(type="symptom", level="medium", message_id=m.id, **kw),
            Alert(type="symptom", level="high", message_id=m.id, **kw),
            Alert(type="fall", level="high", **kw),
        ]
    )
    mark_private(session, [m.id])
    assert sorted((a.type, a.level) for a in visible_alerts(session, 1)) == [
        ("fall", "high"),
        ("symptom", "high"),
    ]


# ---------- memory ----------


def test_memory_follows_its_only_mention_but_stays_if_seen_before(session: Session) -> None:
    m1 = _msg(session, "Linda got bad news", 0)
    m2 = _msg(session, "Rosa is visiting her sister", 5)
    kw = {"elder_id": 1, "kind": "person", "first_seen": T0, "last_seen": T0}
    linda = MemoryItem(
        subject="Linda", text="friend Linda", raw_quote="Linda", message_id=m1.id, **kw
    )
    rosa = MemoryItem(
        subject="Rosa",
        text="Rosa visiting her sister",
        raw_quote="Rosa is visiting her sister",
        message_id=m2.id,
        mention_count=3,
        **kw,
    )
    session.add_all([linda, rosa])
    session.flush()
    mark_private(session, [m1.id, m2.id])
    session.expire_all()
    assert [i.subject for i in visible_memory(session, 1)] == ["Rosa"]
    rosa = session.get_one(MemoryItem, rosa.id)
    assert (rosa.raw_quote, rosa.text, rosa.mention_count) == ("", "Rosa", 2)
