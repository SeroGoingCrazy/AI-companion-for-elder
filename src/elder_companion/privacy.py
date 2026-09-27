"""Parent-controlled privacy (spec 2.7, 3.9): mark on write, filter on read (ADR 18).

Nothing private is ever deleted: `message.private` is set, and symptom occurrences and memory
items take their visibility from their source messages. Every family-facing read (dashboard
routes, summary, reports) goes through the `visible_*` functions here and never queries
message / symptom_* / memory_item directly (spec 5.3).

The red-flag alert path never reads these marks (ADR 19): high-level symptoms stay visible
with their exact quotes even inside a private segment.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from elder_companion.models import Alert, MemoryItem, Message, SymptomLog, SymptomMention
from elder_companion.symptoms.merge import max_severity
from elder_companion.symptoms.schema import SymptomCatalog, get_catalog

PRIVATE_DAY_NOTE = "{nickname} asked to keep part of today's conversation private."
DEFAULT_BYPASS_LEVELS: tuple[str, ...] = ("high",)


# ---------- write side ----------


def privacy_span(
    current_id: int,
    session_user_ids: Sequence[int],
    requested: Iterable[int],
    max_span: int,
) -> list[int]:
    """Which user messages a privacy request covers (spec 3.7).

    `session_user_ids` are the user messages of the current session, oldest first, ending
    with `current_id`. The request may cover the current message and at most `max_span`
    preceding ones; anything else the model named is ignored. An empty request defaults to
    the current message plus the one before it.
    """
    ids = [i for i in session_user_ids if i <= current_id]
    if current_id not in ids:
        ids.append(current_id)
    allowed = ids[-(max_span + 1) :]
    wanted = set(requested) & set(allowed)
    if not wanted:
        wanted = set(allowed[-2:])
    wanted.add(current_id)
    return sorted(wanted)


def mark_private(session: Session, message_ids: Collection[int]) -> int:
    """Mark messages private, and memory items first seen only in them. Returns how many
    messages changed. The caller commits. Summaries need no explicit invalidation: their
    cache key includes the private message ids."""
    if not message_ids:
        return 0
    result = session.execute(
        update(Message)
        .where(Message.id.in_(message_ids), Message.private.is_(False))
        .values(private=True)
    )
    # An item stays visible if any mention was visible; with a single mention it follows it.
    session.execute(
        update(MemoryItem)
        .where(MemoryItem.message_id.in_(message_ids), MemoryItem.mention_count == 1)
        .values(private=True)
    )
    # An item that was visible before this (latest) mention stays visible, but it no longer
    # carries the words she just asked to keep private.
    session.execute(
        update(MemoryItem)
        .where(
            MemoryItem.message_id.in_(message_ids),
            MemoryItem.mention_count > 1,
            MemoryItem.private.is_(False),
        )
        .values(raw_quote="", text=MemoryItem.subject, mention_count=MemoryItem.mention_count - 1)
    )
    return result.rowcount or 0


# ---------- read side: messages ----------


@dataclass(frozen=True)
class MessageView:
    id: int
    role: str
    text: str  # "" when private
    private: bool
    created_at: datetime


def history_page(
    session: Session, elder_id: int, limit: int, before_id: int | None = None
) -> list[MessageView]:
    """The latest `limit` messages (older than `before_id` when paging), oldest first, with
    private ones redacted to a placeholder."""
    stmt = select(Message).where(Message.elder_id == elder_id)
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    rows = reversed(session.scalars(stmt.order_by(Message.id.desc()).limit(limit)).all())
    return [
        MessageView(m.id, m.role, "" if m.private else m.text, m.private, m.created_at)
        for m in rows
    ]


def visible_messages(
    session: Session, elder_id: int, since: datetime, until: datetime
) -> tuple[list[Message], tuple[int, ...]]:
    """Non-private messages in [since, until) (naive UTC), oldest first, and the ids of the
    private ones (so callers can say "part of today was private" and key caches on it)."""
    stmt = (
        select(Message)
        .where(
            Message.elder_id == elder_id,
            Message.created_at >= since,
            Message.created_at < until,
        )
        .order_by(Message.id)
    )
    rows = list(session.scalars(stmt))
    return [m for m in rows if not m.private], tuple(m.id for m in rows if m.private)


# ---------- read side: symptoms ----------


@dataclass(frozen=True)
class MentionView:
    message_id: int | None
    raw_quote: str
    severity: str
    created_at: datetime


@dataclass(frozen=True)
class SymptomView:
    """A symptom_log row as the family may see it: counts, quotes and times come from its
    visible occurrences only (all of them for bypass-level red flags)."""

    id: int
    canonical: str
    label: str
    body_part: str | None
    severity: str
    duration: str | None
    onset: str | None
    status: str
    raw_quote: str
    count: int
    first_seen: datetime
    last_seen: datetime
    red_flag: bool
    mentions: tuple[MentionView, ...]


def _row_view(row: SymptomLog, red_flag: bool, mentions: tuple[MentionView, ...]) -> SymptomView:
    return SymptomView(
        id=row.id,
        canonical=row.canonical,
        label=row.label,
        body_part=row.body_part,
        severity=row.severity,
        duration=row.duration,
        onset=row.onset,
        status=row.status,
        raw_quote=row.raw_quote,
        count=row.count,
        first_seen=row.first_seen,
        last_seen=row.last_seen,
        red_flag=red_flag,
        mentions=mentions,
    )


def symptom_view(
    row: SymptomLog,
    mentions: Sequence[tuple[SymptomMention, bool]],
    *,
    bypass: bool,
    red_flag: bool,
) -> SymptomView | None:
    """Pure: `mentions` are (occurrence, is_private) pairs, oldest first. None = hidden."""
    all_views = tuple(
        MentionView(m.message_id, m.raw_quote, m.severity, m.created_at) for m, _ in mentions
    )
    base = _row_view(row, red_flag, all_views)
    if bypass or not mentions:  # rows logged before Stage H have no occurrences: visible
        return base
    visible = [
        MentionView(m.message_id, m.raw_quote, m.severity, m.created_at)
        for m, private in mentions
        if not private
    ]
    if not visible:
        return None
    if len(visible) == len(mentions):
        return base
    severity = "unknown"
    for v in visible:
        severity = max_severity(severity, v.severity)
    return replace(
        base,
        severity=severity,
        raw_quote=visible[-1].raw_quote,
        count=len(visible),
        first_seen=visible[0].created_at,
        last_seen=visible[-1].created_at,
        mentions=tuple(visible),
    )


def is_bypass(canonical: str, catalog: SymptomCatalog, bypass_levels: Collection[str]) -> bool:
    """Red flags are the only high-level symptoms (symptoms.yaml)."""
    return catalog.is_red_flag(canonical) and "high" in bypass_levels


def visible_symptoms(
    session: Session,
    elder_id: int,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    catalog: SymptomCatalog | None = None,
    bypass_levels: Collection[str] = DEFAULT_BYPASS_LEVELS,
) -> list[SymptomView]:
    """Symptom rows the family may see whose (visible) last mention is in [since, until),
    oldest last-mention first. Bounds are naive UTC."""
    catalog = catalog or get_catalog()
    stmt = select(SymptomLog).where(SymptomLog.elder_id == elder_id)
    if since is not None:
        stmt = stmt.where(SymptomLog.last_seen >= since)
    rows = list(session.scalars(stmt))
    if not rows:
        return []
    mention_stmt = (
        select(SymptomMention, Message.private)
        .outerjoin(Message, SymptomMention.message_id == Message.id)
        .where(SymptomMention.symptom_log_id.in_([r.id for r in rows]))
        .order_by(SymptomMention.created_at, SymptomMention.id)
    )
    by_row: dict[int, list[tuple[SymptomMention, bool]]] = defaultdict(list)
    for mention, private in session.execute(mention_stmt):
        by_row[mention.symptom_log_id].append((mention, bool(private)))

    views = []
    for row in rows:
        view = symptom_view(
            row,
            by_row.get(row.id, []),
            bypass=is_bypass(row.canonical, catalog, bypass_levels),
            red_flag=catalog.is_red_flag(row.canonical),
        )
        if view is None:
            continue
        if since is not None and view.last_seen < since:
            continue
        if until is not None and view.last_seen >= until:
            continue
        views.append(view)
    return sorted(views, key=lambda v: (v.last_seen, v.id))


# ---------- read side: alerts ----------


def _alert_visible(alert: Alert, private_ids: set[int], bypass_levels: Collection[str]) -> bool:
    return (
        alert.type != "symptom"
        or alert.level in bypass_levels
        or alert.message_id is None
        or alert.message_id not in private_ids
    )


def visible_alerts(
    session: Session,
    elder_id: int,
    *,
    limit: int = 50,
    since: datetime | None = None,
    until: datetime | None = None,
    bypass_levels: Collection[str] = DEFAULT_BYPASS_LEVELS,
) -> list[Alert]:
    """Newest first. Fall alerts and bypass-level symptom alerts are always visible; other
    symptom alerts raised by a private message are hidden."""
    stmt = select(Alert).where(Alert.elder_id == elder_id)
    if since is not None:
        stmt = stmt.where(Alert.created_at >= since)
    if until is not None:
        stmt = stmt.where(Alert.created_at < until)
    stmt = stmt.order_by(Alert.created_at.desc(), Alert.id.desc())
    alerts = list(session.scalars(stmt))
    source_ids = {a.message_id for a in alerts if a.message_id is not None}
    private_ids = (
        set(
            session.scalars(
                select(Message.id).where(Message.id.in_(source_ids), Message.private.is_(True))
            )
        )
        if source_ids
        else set()
    )
    return [a for a in alerts if _alert_visible(a, private_ids, bypass_levels)][:limit]


def alert_is_visible(
    session: Session, alert: Alert, bypass_levels: Collection[str] = DEFAULT_BYPASS_LEVELS
) -> bool:
    if alert.message_id is None:
        return True
    msg = session.get(Message, alert.message_id)
    return _alert_visible(alert, {msg.id} if msg and msg.private else set(), bypass_levels)


# ---------- read side: memory ----------


def visible_memory(
    session: Session, elder_id: int, kinds: Collection[str] | None = None
) -> list[MemoryItem]:
    """Non-private memory items, oldest first."""
    stmt = select(MemoryItem).where(MemoryItem.elder_id == elder_id, MemoryItem.private.is_(False))
    if kinds:
        stmt = stmt.where(MemoryItem.kind.in_(list(kinds)))
    return list(session.scalars(stmt.order_by(MemoryItem.first_seen, MemoryItem.id)))
