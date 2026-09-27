"""Family reminders and adherence (spec 2.6, 3.8, H5).

A reminder is due on a local day once its time has passed (daily) or the day has arrived
(one-off). The agenda raises it in the next greeting; her answer lands in `reminder_log`.
Anything still `mentioned` when the day is over becomes `no_response`.

The scheduling rules at the top are pure functions so they can be unit-tested without a DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.db import utcnow
from elder_companion.models import FamilyMember, Reminder, ReminderLog

OPEN_STATUS = "mentioned"
RESOLVED_STATUSES = ("confirmed", "declined")
ADHERENCE_DAYS = 7


class ReminderError(LookupError):
    """The reminder or family member does not exist for this elder."""


# ---------- pure scheduling rules ----------


def parse_hhmm(value: str) -> time:
    """Parse "08:00" into time(8, 0). Raises ValueError on anything else."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("schedule_time must look like HH:MM")
    hour, minute = (int(p) for p in parts)
    return time(hour, minute)  # time() rejects out-of-range values itself


def is_due(reminder: Reminder, now: datetime) -> bool:
    """`now` is aware, in the elder's zone. Daily reminders come due once their time has
    passed today; a one-off is due from its date onward (so a missed day still gets raised)."""
    if not reminder.active:
        return False
    if reminder.schedule_date is not None:
        return reminder.schedule_date <= now.date()
    if reminder.schedule_time is None:
        return False
    try:
        return parse_hhmm(reminder.schedule_time) <= now.time()
    except ValueError:
        return False


def due_sort_key(reminder: Reminder) -> tuple[int, str, int]:
    """One-off reminders first (they expire), then daily by time of day."""
    if reminder.schedule_date is not None:
        return (0, reminder.schedule_date.isoformat(), reminder.id)
    return (1, reminder.schedule_time or "", reminder.id)


# ---------- views ----------


@dataclass(frozen=True)
class DayStatus:
    date: date
    status: str | None  # None = not raised that day


@dataclass(frozen=True)
class ReminderView:
    id: int
    text: str
    schedule_time: str | None
    schedule_date: date | None
    active: bool
    from_member_id: int | None
    from_member_name: str | None
    created_at: datetime
    today_status: str | None
    history: tuple[DayStatus, ...]  # oldest first, ADHERENCE_DAYS long
    confirmed_days: int
    raised_days: int


# ---------- queries ----------


def active_reminders(session: Session, elder_id: int) -> list[Reminder]:
    stmt = select(Reminder).where(Reminder.elder_id == elder_id, Reminder.active.is_(True))
    return list(session.scalars(stmt))


def due_today(session: Session, elder_id: int, now: datetime) -> list[Reminder]:
    """Active reminders that are due and have not been raised yet on `now`'s local day."""
    raised = _logged_ids(session, elder_id, now.date())
    due = [r for r in active_reminders(session, elder_id) if r.id not in raised and is_due(r, now)]
    return sorted(due, key=due_sort_key)


def _logged_ids(session: Session, elder_id: int, day: date) -> set[int]:
    stmt = (
        select(ReminderLog.reminder_id)
        .join(Reminder, Reminder.id == ReminderLog.reminder_id)
        .where(Reminder.elder_id == elder_id, ReminderLog.date == day)
    )
    return set(session.scalars(stmt))


def open_logs(session: Session, elder_id: int, day: date) -> list[ReminderLog]:
    """Reminders raised on `day` that she has not answered yet: what an ack can resolve."""
    stmt = (
        select(ReminderLog)
        .join(Reminder, Reminder.id == ReminderLog.reminder_id)
        .where(
            Reminder.elder_id == elder_id,
            ReminderLog.date == day,
            ReminderLog.status == OPEN_STATUS,
        )
        .order_by(ReminderLog.id)
    )
    return list(session.scalars(stmt))


# ---------- writes ----------


def create_reminder(
    session: Session,
    elder_id: int,
    text: str,
    *,
    from_member_id: int | None = None,
    schedule_time: str | None = None,
    schedule_date: date | None = None,
) -> Reminder:
    if schedule_time is None and schedule_date is None:
        raise ValueError("a reminder needs either schedule_time or schedule_date")
    if schedule_time is not None:
        schedule_time = parse_hhmm(schedule_time).strftime("%H:%M")
    if from_member_id is not None and not _member_exists(session, elder_id, from_member_id):
        raise ReminderError(f"family member {from_member_id} not found")
    reminder = Reminder(
        elder_id=elder_id,
        from_member_id=from_member_id,
        text=text.strip(),
        schedule_time=schedule_time,
        schedule_date=schedule_date,
    )
    session.add(reminder)
    session.commit()
    return reminder


def deactivate(session: Session, elder_id: int, reminder_id: int) -> Reminder:
    reminder = session.get(Reminder, reminder_id)
    if reminder is None or reminder.elder_id != elder_id:
        raise ReminderError(f"reminder {reminder_id} not found")
    reminder.active = False
    session.commit()
    return reminder


def mark_mentioned(session: Session, reminder_id: int, day: date) -> ReminderLog:
    """Record that the greeting raised this reminder. Idempotent per (reminder, day)."""
    existing = session.scalars(
        select(ReminderLog).where(ReminderLog.reminder_id == reminder_id, ReminderLog.date == day)
    ).first()
    if existing is not None:
        return existing
    log = ReminderLog(reminder_id=reminder_id, date=day, status=OPEN_STATUS)
    session.add(log)
    session.flush()
    return log


def record_ack(
    session: Session, log: ReminderLog, status: str, message_id: int | None = None
) -> ReminderLog:
    """Apply her answer. Only an open (`mentioned`) row moves, so a later "no" cannot
    overwrite an earlier "yes" from the same day."""
    if status not in RESOLVED_STATUSES:
        raise ValueError(f"an ack must be one of {RESOLVED_STATUSES}, got {status!r}")
    if log.status != OPEN_STATUS:
        return log
    log.status = status
    log.message_id = message_id
    log.updated_at = utcnow()
    session.flush()
    return log


def sweep_no_response(session: Session, elder_id: int, today: date) -> list[ReminderLog]:
    """Days before `today` that she never answered are closed as `no_response` (spec 3.8)."""
    stmt = (
        select(ReminderLog)
        .join(Reminder, Reminder.id == ReminderLog.reminder_id)
        .where(
            Reminder.elder_id == elder_id,
            ReminderLog.date < today,
            ReminderLog.status == OPEN_STATUS,
        )
    )
    stale = list(session.scalars(stmt))
    for log in stale:
        log.status = "no_response"
        log.updated_at = utcnow()
    return stale


# ---------- adherence ----------


def list_with_adherence(
    session: Session, elder_id: int, today: date, days: int = ADHERENCE_DAYS
) -> list[ReminderView]:
    """Every reminder (active first) with the last `days` local days of status."""
    stmt = (
        select(Reminder)
        .where(Reminder.elder_id == elder_id)
        .order_by(Reminder.active.desc(), Reminder.id)
    )
    reminders = list(session.scalars(stmt))
    if not reminders:
        return []
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    logs = _logs_by_reminder(session, [r.id for r in reminders], window[0])
    names = _member_names(session, elder_id)
    views = []
    for r in reminders:
        by_day = logs.get(r.id, {})
        history = tuple(DayStatus(d, by_day.get(d)) for d in window)
        raised = [h for h in history if h.status is not None]
        views.append(
            ReminderView(
                id=r.id,
                text=r.text,
                schedule_time=r.schedule_time,
                schedule_date=r.schedule_date,
                active=r.active,
                from_member_id=r.from_member_id,
                from_member_name=names.get(r.from_member_id),
                created_at=r.created_at,
                today_status=by_day.get(today),
                history=history,
                confirmed_days=sum(h.status == "confirmed" for h in raised),
                raised_days=len(raised),
            )
        )
    return views


def _logs_by_reminder(
    session: Session, reminder_ids: list[int], since: date
) -> dict[int, dict[date, str]]:
    stmt = select(ReminderLog).where(
        ReminderLog.reminder_id.in_(reminder_ids), ReminderLog.date >= since
    )
    out: dict[int, dict[date, str]] = {}
    for log in session.scalars(stmt):
        out.setdefault(log.reminder_id, {})[log.date] = log.status
    return out


def _member_names(session: Session, elder_id: int) -> dict[int, str]:
    stmt = select(FamilyMember).where(FamilyMember.elder_id == elder_id)
    return {m.id: m.name for m in session.scalars(stmt)}


def _member_exists(session: Session, elder_id: int, member_id: int) -> bool:
    member = session.get(FamilyMember, member_id)
    return member is not None and member.elder_id == elder_id
