"""Companion memory for one elder message: extract -> privacy marks -> merge (spec 3.7, 5.4)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from elder_companion.db import utcnow
from elder_companion.family_loop import reminders as reminders_repo
from elder_companion.llm import LLMError
from elder_companion.memory.extractor import (
    MemoryExtractor,
    OpenReminder,
    Turn,
    normalize_subject,
)
from elder_companion.memory.schema import MemoryItemIn, ReminderAck
from elder_companion.models import MemoryItem, Message, Reminder, ReminderLog
from elder_companion.privacy import mark_private, privacy_span
from elder_companion.reports.digest import invalidate
from elder_companion.settings import Settings

logger = logging.getLogger(__name__)

CONTEXT_TURNS = 3  # earlier user+assistant pairs given to the extractor


# ---------- pure merge rules ----------


def same_item(existing: MemoryItem, item: MemoryItemIn) -> bool:
    if existing.kind != item.kind:
        return False
    if item.kind == "story":  # each story is its own keepsake; only exact repeats merge
        return normalize_subject(existing.raw_quote) == normalize_subject(item.raw_quote)
    return normalize_subject(existing.subject) == normalize_subject(item.subject)


def find_match(
    candidates: Iterable[MemoryItem], item: MemoryItemIn, *, private: bool
) -> MemoryItem | None:
    """The item a new mention merges into. A private mention never merges into a visible
    item (its words would show up in the family's view), so it gets a private twin; a
    visible mention may merge into a private item, which then becomes visible."""
    matches = [c for c in candidates if same_item(c, item)]
    same_visibility = [c for c in matches if c.private == private]
    if same_visibility:
        return max(same_visibility, key=lambda c: c.last_seen)
    if not private and matches:
        return max(matches, key=lambda c: c.last_seen)
    return None


def follow_up_due(item: MemoryItemIn, today: date, default_delay_days: int) -> date:
    days = item.due_in_days if item.due_in_days is not None else default_delay_days
    return today + timedelta(days=max(0, days))


@dataclass(frozen=True)
class MergeResult:
    is_new: bool
    values: dict[str, Any]  # column -> value, to insert (is_new) or to set on `existing`


def merge_memory_item(
    existing: MemoryItem | None,
    item: MemoryItemIn,
    now: datetime,
    *,
    today: date,
    message_id: int | None,
    private: bool,
    default_delay_days: int = 1,
) -> MergeResult:
    """`now` is naive UTC; `today` is the elder's local date (for follow-up due dates)."""
    due = follow_up_due(item, today, default_delay_days) if item.kind == "follow_up" else None
    if existing is None:
        return MergeResult(
            is_new=True,
            values={
                "kind": item.kind,
                "subject": item.subject.strip(),
                "text": item.text.strip(),
                "raw_quote": item.raw_quote,
                "message_id": message_id,
                "private": private,
                "mention_count": 1,
                "due_date": due,
                "status": "open",
                "first_seen": now,
                "last_seen": now,
            },
        )
    values: dict[str, Any] = {
        "text": item.text.strip(),
        "raw_quote": item.raw_quote,
        "message_id": message_id,
        "private": existing.private and private,
        "mention_count": existing.mention_count + 1,
        "last_seen": now,
    }
    if item.kind == "follow_up":
        # Still open: move the date. Already asked/expired: only an explicit new date
        # reopens it (talking about how it went must not make her be asked again).
        if existing.status == "open":
            values["due_date"] = due
        elif item.due_in_days is not None:
            values["due_date"] = due
            values["status"] = "open"
    return MergeResult(is_new=False, values=values)


# ---------- service ----------


@dataclass
class MemoryResult:
    items: list[MemoryItem] = field(default_factory=list)  # rows inserted or updated
    marked_private: list[int] = field(default_factory=list)  # message ids
    acked_reminders: list[int] = field(default_factory=list)  # reminder ids she answered


class MemoryService:
    def __init__(
        self,
        session: Session,
        extractor: MemoryExtractor,
        settings: Settings,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session = session
        self._extractor = extractor
        self._s = settings
        self._now = now

    def process_message(self, message_id: int) -> MemoryResult:
        """Raises LLMError if extraction fails (nothing is written then)."""
        result = MemoryResult()
        msg = self._session.get(Message, message_id)
        if msg is None or msg.role != "user":
            return result
        today = self._today()
        open_logs = reminders_repo.open_logs(self._session, msg.elder_id, today)
        extraction = self._extractor.extract(
            msg.id, msg.text, self._recent_turns(msg), self._open_reminders(open_logs)
        )
        now = self._now()
        result.acked_reminders = self._apply_acks(extraction.reminder_acks, open_logs, msg.id)

        request = extraction.privacy_request
        if request.requested:
            covered = privacy_span(
                msg.id,
                self._session_user_ids(msg),
                request.covers_message_ids,
                self._s.privacy.max_span_user_messages,
            )
            result.marked_private = covered
            hidden = with_replies(self._session, msg.elder_id, covered)
            mark_private(self._session, hidden)
            # Those days were very likely already summarized with this content in them.
            invalidate(self._session, msg.elder_id, self._local_days(hidden))
            self._session.flush()
            self._session.refresh(msg)

        for item in extraction.items:
            result.items.append(self._merge(msg, item, now, today))
        self._session.commit()
        return result

    def _local_days(self, message_ids: Sequence[int]) -> set[date]:
        rows = self._session.scalars(select(Message).where(Message.id.in_(list(message_ids))))
        tz = self._s.chat.tz
        return {m.created_at.replace(tzinfo=UTC).astimezone(tz).date() for m in rows}

    def _today(self) -> date:
        """The elder's local date, from the same clock the rest of the service uses."""
        return self._now().replace(tzinfo=UTC).astimezone(self._s.chat.tz).date()

    def _open_reminders(self, logs: Sequence[ReminderLog]) -> list[OpenReminder]:
        texts = (
            {
                r.id: r.text
                for r in self._session.scalars(
                    select(Reminder).where(Reminder.id.in_([log.reminder_id for log in logs]))
                )
            }
            if logs
            else {}
        )
        return [
            OpenReminder(log.reminder_id, texts[log.reminder_id])
            for log in logs
            if log.reminder_id in texts
        ]

    def _apply_acks(
        self, acks: Sequence[ReminderAck], logs: Sequence[ReminderLog], message_id: int
    ) -> list[int]:
        by_reminder = {log.reminder_id: log for log in logs}
        applied = []
        for ack in acks:
            log = by_reminder.get(ack.reminder_id)
            if log is None:
                continue
            reminders_repo.record_ack(self._session, log, ack.status, message_id)
            applied.append(ack.reminder_id)
        return applied

    def _merge(self, msg: Message, item: MemoryItemIn, now: datetime, today: date) -> MemoryItem:
        stmt = select(MemoryItem).where(
            MemoryItem.elder_id == msg.elder_id, MemoryItem.kind == item.kind
        )
        existing = find_match(self._session.scalars(stmt), item, private=msg.private)
        merged = merge_memory_item(
            existing,
            item,
            now,
            today=today,
            message_id=msg.id,
            private=msg.private,
            default_delay_days=self._s.memory.follow_up_default_delay_days,
        )
        if merged.is_new:
            row = MemoryItem(elder_id=msg.elder_id, **merged.values)
            self._session.add(row)
        else:
            row = existing
            for key, value in merged.values.items():
                setattr(row, key, value)
        self._session.flush()
        return row

    def _recent_turns(self, msg: Message) -> list[Turn]:
        stmt = (
            select(Message)
            .where(Message.elder_id == msg.elder_id, Message.id < msg.id)
            .order_by(Message.id.desc())
            .limit(2 * CONTEXT_TURNS)
        )
        return [Turn(m.id, m.role, m.text) for m in reversed(self._session.scalars(stmt).all())]

    def _session_user_ids(self, msg: Message) -> list[int]:
        stmt = select(Message.id).where(
            Message.elder_id == msg.elder_id, Message.role == "user", Message.id <= msg.id
        )
        if msg.session_id is None:
            stmt = stmt.where(Message.session_id.is_(None))
        else:
            stmt = stmt.where(Message.session_id == msg.session_id)
        limit = self._s.privacy.max_span_user_messages + 1
        return sorted(self._session.scalars(stmt.order_by(Message.id.desc()).limit(limit)))


def with_replies(session: Session, elder_id: int, user_ids: Iterable[int]) -> list[int]:
    """The covered user messages plus the companion's reply right after each one: a reply
    like "I'm sorry to hear about Linda" must be hidden too."""
    ids = set(user_ids)
    for uid in list(ids):
        nxt = session.scalars(
            select(Message)
            .where(Message.elder_id == elder_id, Message.id > uid)
            .order_by(Message.id)
            .limit(1)
        ).first()
        if nxt is not None and nxt.role == "assistant":
            ids.add(nxt.id)
    return sorted(ids)


def process_message_memory(
    session_factory: sessionmaker[Session],
    extractor: MemoryExtractor,
    settings: Settings,
    message_id: int,
) -> MemoryResult | None:
    """Background task after a chat turn. Never raises: a failure here must not affect chat
    or the symptom pipeline."""
    try:
        with session_factory() as session:
            return MemoryService(session, extractor, settings).process_message(message_id)
    except LLMError:
        logger.warning("memory extraction failed for message %s", message_id, exc_info=True)
    except Exception:
        logger.exception("memory pipeline crashed for message %s", message_id)
    return None
