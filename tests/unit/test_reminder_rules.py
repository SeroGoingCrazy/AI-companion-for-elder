"""H5: reminder scheduling rules and agenda priority (pure functions, no DB)."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from elder_companion.agenda.select import reminder_item, select_agenda
from elder_companion.family_loop.reminders import due_sort_key, is_due, parse_hhmm
from elder_companion.models import MemoryItem, Reminder

pytestmark = pytest.mark.unit

TZ = ZoneInfo("America/Los_Angeles")
TODAY = date(2026, 9, 26)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 26, hour, minute, tzinfo=TZ)


def daily(hhmm: str, *, id: int = 1, active: bool = True) -> Reminder:
    return Reminder(id=id, elder_id=1, text="pill", schedule_time=hhmm, active=active)


def once(on: date, *, id: int = 1) -> Reminder:
    return Reminder(id=id, elder_id=1, text="eye doctor", schedule_date=on, active=True)


def follow_up(subject: str, due: date, *, id: int = 1) -> MemoryItem:
    return MemoryItem(
        id=id,
        elder_id=1,
        kind="follow_up",
        subject=subject,
        text=f"{subject} plan",
        raw_quote=subject,
        due_date=due,
        status="open",
        first_seen=datetime(2026, 9, 20),
    )


@pytest.mark.parametrize("value,expected", [("08:00", time(8, 0)), ("8:05", time(8, 5))])
def test_parse_hhmm(value: str, expected: time) -> None:
    assert parse_hhmm(value) == expected


@pytest.mark.parametrize("value", ["", "8", "08:00:00", "24:00", "08:61", "abc", "-1:00"])
def test_parse_hhmm_rejects_junk(value: str) -> None:
    with pytest.raises(ValueError):
        parse_hhmm(value)


def test_daily_reminder_comes_due_once_its_time_has_passed() -> None:
    assert not is_due(daily("08:00"), at(7, 59))
    assert is_due(daily("08:00"), at(8, 0))
    assert is_due(daily("08:00"), at(21, 30))


def test_inactive_reminder_is_never_due() -> None:
    assert not is_due(daily("08:00", active=False), at(12))


def test_one_off_is_due_from_its_date_onward() -> None:
    """A missed day still gets raised: she may not have opened the app that day."""
    yesterday = datetime(2026, 9, 25, 12, tzinfo=TZ)
    assert not is_due(once(TODAY), yesterday)
    assert is_due(once(TODAY), at(0, 0))
    assert is_due(once(date(2026, 9, 20)), at(9))


def test_a_reminder_with_no_schedule_is_never_due() -> None:
    assert not is_due(Reminder(id=1, elder_id=1, text="x", active=True), at(12))


def test_one_off_reminders_sort_before_daily_ones() -> None:
    reminders = [daily("07:00", id=2), once(TODAY, id=3), daily("06:00", id=4)]
    assert [r.id for r in sorted(reminders, key=due_sort_key)] == [3, 4, 2]


def test_reminders_outrank_follow_ups_and_the_rest_carries_over() -> None:
    items = select_agenda(
        [follow_up("orchid", TODAY, id=10), follow_up("choir", TODAY, id=11)],
        TODAY,
        budget=2,
        reminders=[reminder_item(daily("08:00", id=1), "Amy")],
    )
    assert [(i.kind, i.ref_id) for i in items] == [("reminder", 1), ("follow_up", 10)]


def test_budget_of_zero_selects_nothing() -> None:
    assert (
        select_agenda([follow_up("orchid", TODAY)], TODAY, 0, [reminder_item(daily("08:00"))]) == []
    )


def test_reminder_item_carries_the_family_wording_and_author() -> None:
    item = reminder_item(daily("08:00", id=7), "Amy")
    assert item.kind == "reminder" and item.ref_id == 7
    assert item.text == "pill" and item.from_member_name == "Amy"
    assert item.private is False  # a family-set reminder is never private
