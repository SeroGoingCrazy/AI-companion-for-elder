"""Agenda state in the DB: expire stale follow-ups, pick the greeting's items, mark them."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.agenda.select import AgendaItem, is_stale, select_agenda
from elder_companion.models import MemoryItem
from elder_companion.settings import AgendaSettings, MemorySettings


class AgendaService:
    def __init__(self, session: Session, agenda: AgendaSettings, memory: MemorySettings) -> None:
        self._session = session
        self._agenda = agenda
        self._memory = memory

    def select(self, elder_id: int, today: date) -> list[AgendaItem]:
        """Items for the next greeting (`today` is the elder's local date). Expires stale
        follow-ups on the way; the caller commits."""
        stmt = select(MemoryItem).where(
            MemoryItem.elder_id == elder_id,
            MemoryItem.kind == "follow_up",
            MemoryItem.status == "open",
        )
        open_items = list(self._session.scalars(stmt))
        for item in open_items:
            if is_stale(item, today, self._memory.follow_up_expire_days):
                item.status = "expired"
        live = [i for i in open_items if i.status == "open"]
        return select_agenda(live, today, self._agenda.max_items_per_greet)

    def mark_carried(self, items: Sequence[AgendaItem]) -> None:
        """After the greeting is saved: follow-ups it carried are not asked again."""
        for item in items:
            row = self._session.get(MemoryItem, item.ref_id)
            if row is not None and row.status == "open":
                row.status = "asked"
