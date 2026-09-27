"""What the next greeting brings up (spec 3.8). Pure functions: callers do the DB work.

Priority is reminders (set by the family) before follow-ups (things she said herself), oldest
first within a kind. Whatever does not fit the budget carries over to the next greeting.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from elder_companion.models import MemoryItem, Reminder

# Reminders come first: the family is waiting on an answer, and a follow-up can wait a day.
AgendaKind = Literal["reminder", "follow_up"]


@dataclass(frozen=True)
class AgendaItem:
    kind: AgendaKind
    ref_id: int
    subject: str
    text: str
    due_date: date | None
    private: bool = False
    from_member_name: str | None = None  # reminders: "Amy wanted me to check ..."


def is_due(item: MemoryItem, today: date) -> bool:
    return (
        item.kind == "follow_up"
        and item.status == "open"
        and item.due_date is not None
        and item.due_date <= today
    )


def is_stale(item: MemoryItem, today: date, expire_days: int) -> bool:
    """An open follow-up never asked this long after it was due is dropped."""
    return is_due(item, today) and today - item.due_date > timedelta(days=expire_days)


def reminder_item(reminder: Reminder, member_name: str | None = None) -> AgendaItem:
    """`subject` is what the family typed; the companion never adds to it."""
    return AgendaItem(
        kind="reminder",
        ref_id=reminder.id,
        subject=reminder.text,
        text=reminder.text,
        due_date=reminder.schedule_date,
        private=False,
        from_member_name=member_name,
    )


def follow_up_item(item: MemoryItem) -> AgendaItem:
    return AgendaItem("follow_up", item.id, item.subject, item.text, item.due_date, item.private)


def select_agenda(
    follow_ups: Sequence[MemoryItem],
    today: date,
    budget: int,
    reminders: Sequence[AgendaItem] = (),
) -> list[AgendaItem]:
    """Due reminders then due follow-ups, oldest first, at most `budget`. The rest carry over.

    `reminders` is already filtered and ordered by the caller (it needs the clock, not just
    the date); follow-ups are filtered here.
    """
    due = sorted(
        (f for f in follow_ups if is_due(f, today)),
        key=lambda f: (f.due_date, f.first_seen, f.id),
    )
    items = [*reminders, *(follow_up_item(f) for f in due)]
    return items[: max(0, budget)]
