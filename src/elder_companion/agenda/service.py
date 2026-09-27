"""Agenda state in the DB: expire stale follow-ups, pick the greeting's items, mark them."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.agenda.select import AgendaItem, is_stale, reminder_item, select_agenda
from elder_companion.family_loop import reminders as reminders_repo
from elder_companion.models import FamilyMember, MemoryItem
from elder_companion.settings import AgendaSettings, MemorySettings


class AgendaService:
    def __init__(self, session: Session, agenda: AgendaSettings, memory: MemorySettings) -> None:
        self._session = session
        self._agenda = agenda
        self._memory = memory
        self._member_cache: dict[int, str] | None = None

    def select(self, elder_id: int, now: datetime) -> list[AgendaItem]:
        """Items for the next greeting (`now` is aware, in the elder's zone). Expires stale
        follow-ups and closes unanswered earlier days on the way; the caller commits."""
        today = now.date()
        reminders_repo.sweep_no_response(self._session, elder_id, today)
        due_reminders = [
            reminder_item(r, self._member_name(r.from_member_id))
            for r in reminders_repo.due_today(self._session, elder_id, now)
        ]
        return select_agenda(
            self._open_follow_ups(elder_id, today),
            today,
            self._agenda.max_items_per_greet,
            due_reminders,
        )

    def _open_follow_ups(self, elder_id: int, today: date) -> list[MemoryItem]:
        stmt = select(MemoryItem).where(
            MemoryItem.elder_id == elder_id,
            MemoryItem.kind == "follow_up",
            MemoryItem.status == "open",
        )
        open_items = list(self._session.scalars(stmt))
        for item in open_items:
            if is_stale(item, today, self._memory.follow_up_expire_days):
                item.status = "expired"
        return [i for i in open_items if i.status == "open"]

    def mark_carried(self, items: Sequence[AgendaItem], today: date) -> None:
        """After the greeting is saved: follow-ups it carried are not asked again, and each
        reminder it carried opens a `reminder_log` row waiting for her answer."""
        for item in items:
            if item.kind == "reminder":
                reminders_repo.mark_mentioned(self._session, item.ref_id, today)
                continue
            row = self._session.get(MemoryItem, item.ref_id)
            if row is not None and row.status == "open":
                row.status = "asked"

    def _member_name(self, member_id: int | None) -> str | None:
        """Loaded once per greeting: most elders have a handful of family members."""
        if member_id is None:
            return None
        if self._member_cache is None:
            rows = self._session.scalars(select(FamilyMember))
            self._member_cache = {m.id: m.name for m in rows}
        return self._member_cache.get(member_id)
