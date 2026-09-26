"""Read models for the family dashboard. Days are calendar days in the elder's time zone."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.models import Message, SymptomLog


def local_day_bounds(day: date, now: datetime) -> tuple[datetime, datetime]:
    """[start, end) of a local calendar day as naive UTC, for DB comparisons. `now` carries
    the elder's zone."""
    start = datetime.combine(day, time.min, tzinfo=now.tzinfo)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=now.tzinfo)
    return (
        start.astimezone(UTC).replace(tzinfo=None),
        end.astimezone(UTC).replace(tzinfo=None),
    )


def to_local_date(ts_utc_naive: datetime, now: datetime) -> date:
    return ts_utc_naive.replace(tzinfo=UTC).astimezone(now.tzinfo).date()


def day_label(day: date, today: date) -> str:
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    return f"{day:%a}, {day:%b} {day.day}"  # e.g. "Sat, Sep 26"


@dataclass
class DayGroup:
    date: date
    label: str
    symptoms: list[SymptomLog] = field(default_factory=list)


def group_by_day(rows: list[SymptomLog], now: datetime, days: int) -> list[DayGroup]:
    """One group per day for the last `days` days, newest first; each row sits on the day it
    was last mentioned. Empty days are kept so the timeline has no gaps."""
    today = now.date()
    groups = [
        DayGroup(d, day_label(d, today)) for d in (today - timedelta(days=i) for i in range(days))
    ]
    by_date = {g.date: g for g in groups}
    for row in sorted(rows, key=lambda r: r.last_seen, reverse=True):
        group = by_date.get(to_local_date(row.last_seen, now))
        if group is not None:
            group.symptoms.append(row)
    return groups


def symptom_timeline(session: Session, elder_id: int, now: datetime, days: int) -> list[DayGroup]:
    since, _ = local_day_bounds(now.date() - timedelta(days=days - 1), now)
    stmt = select(SymptomLog).where(SymptomLog.elder_id == elder_id, SymptomLog.last_seen >= since)
    return group_by_day(list(session.scalars(stmt)), now, days)


def symptoms_on(session: Session, elder_id: int, day: date, now: datetime) -> list[SymptomLog]:
    start, end = local_day_bounds(day, now)
    stmt = (
        select(SymptomLog)
        .where(
            SymptomLog.elder_id == elder_id,
            SymptomLog.last_seen >= start,
            SymptomLog.last_seen < end,
        )
        .order_by(SymptomLog.last_seen)
    )
    return list(session.scalars(stmt))


def messages_on(session: Session, elder_id: int, day: date, now: datetime) -> list[Message]:
    start, end = local_day_bounds(day, now)
    stmt = (
        select(Message)
        .where(Message.elder_id == elder_id, Message.created_at >= start, Message.created_at < end)
        .order_by(Message.id)
    )
    return list(session.scalars(stmt))


def recent_messages(
    session: Session, elder_id: int, limit: int, before_id: int | None = None
) -> list[Message]:
    """The latest `limit` messages (older than `before_id` when paging), oldest first."""
    stmt = select(Message).where(Message.elder_id == elder_id)
    if before_id is not None:
        stmt = stmt.where(Message.id < before_id)
    stmt = stmt.order_by(Message.id.desc()).limit(limit)
    return list(reversed(session.scalars(stmt).all()))
