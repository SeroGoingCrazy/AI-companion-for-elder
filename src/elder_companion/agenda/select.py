"""What the next greeting brings up (spec 3.8). Pure functions: callers do the DB work.

Reminders are not part of this version, so the agenda holds follow-ups only; the item kind is
kept so reminders can slot in ahead of them later.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from elder_companion.models import MemoryItem


@dataclass(frozen=True)
class AgendaItem:
    kind: Literal["follow_up"]
    ref_id: int
    subject: str
    text: str
    due_date: date
    private: bool = False


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


def select_agenda(follow_ups: Sequence[MemoryItem], today: date, budget: int) -> list[AgendaItem]:
    """Due follow-ups, oldest due first, at most `budget`. The rest carry over."""
    due = sorted(
        (f for f in follow_ups if is_due(f, today)),
        key=lambda f: (f.due_date, f.first_seen, f.id),
    )
    return [
        AgendaItem("follow_up", f.id, f.subject, f.text, f.due_date, f.private)
        for f in due[: max(0, budget)]
    ]
