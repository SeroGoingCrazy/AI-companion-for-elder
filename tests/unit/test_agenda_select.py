"""H3: agenda selection, budget, ordering and expiry (pure)."""

from datetime import date, datetime, timedelta

import pytest

from elder_companion.agenda.select import is_stale, select_agenda
from elder_companion.chat.context import PRIVATE_TAG, format_agenda, format_memory
from elder_companion.models import MemoryItem

pytestmark = pytest.mark.unit

TODAY = date(2026, 9, 29)
T0 = datetime(2026, 9, 20, 12, 0)


def _f(id: int, due: date | None, status: str = "open", **kw) -> MemoryItem:
    base = {
        "id": id,
        "elder_id": 1,
        "kind": "follow_up",
        "subject": f"plan {id}",
        "text": "t",
        "raw_quote": "q",
        "private": False,
        "mention_count": 1,
        "due_date": due,
        "status": status,
        "first_seen": T0 + timedelta(minutes=id),
        "last_seen": T0,
    }
    return MemoryItem(**{**base, **kw})


def test_only_open_due_follow_ups_oldest_first_within_budget() -> None:
    items = [
        _f(1, TODAY),
        _f(2, TODAY - timedelta(days=2)),
        _f(3, TODAY + timedelta(days=1)),  # not due yet
        _f(4, TODAY - timedelta(days=1), status="asked"),
        _f(5, None),
        _f(6, TODAY - timedelta(days=1)),
        _f(7, TODAY),
        _f(8, TODAY, kind="person"),
    ]
    agenda = select_agenda(items, TODAY, budget=3)
    assert [a.ref_id for a in agenda] == [2, 6, 1]  # 7 carries over to the next greeting
    assert select_agenda(items, TODAY, budget=0) == []


def test_follow_ups_expire_after_the_window() -> None:
    assert is_stale(_f(1, TODAY - timedelta(days=8)), TODAY, expire_days=7)
    assert not is_stale(_f(1, TODAY - timedelta(days=7)), TODAY, expire_days=7)
    assert not is_stale(_f(1, TODAY - timedelta(days=30), status="asked"), TODAY, 7)


def test_private_items_are_tagged_never_relay() -> None:
    [a] = select_agenda([_f(1, TODAY, private=True)], TODAY, 3)
    assert PRIVATE_TAG in format_agenda([a])
    line = format_memory(_f(2, None, kind="person", subject="Linda", text="friend", private=True))
    assert line.startswith("- Linda (person): friend") and PRIVATE_TAG in line


def test_memory_lines() -> None:
    assert format_memory(_f(1, TODAY, subject="orchid", text="repotting")) == (
        "- orchid (plan): repotting, worth asking about from Tuesday, September 29"
    )
    assert "(you already asked once)" in format_memory(_f(1, TODAY, status="asked"))
    rosa = _f(2, None, kind="person", subject="Rosa", text="neighbor", mention_count=3)
    assert format_memory(rosa) == "- Rosa (person): neighbor, mentioned 3 times"
