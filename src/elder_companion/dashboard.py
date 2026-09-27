"""Read models for the family dashboard. Days are calendar days in the elder's time zone.

Data comes through `privacy` (the only family-facing read path).
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.orm import Session

from elder_companion.privacy import DEFAULT_BYPASS_LEVELS, SymptomView, visible_symptoms


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
    symptoms: list[SymptomView] = field(default_factory=list)


def group_by_day(rows: list[SymptomView], now: datetime, days: int) -> list[DayGroup]:
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


def symptom_timeline(
    session: Session,
    elder_id: int,
    now: datetime,
    days: int,
    bypass_levels: Collection[str] = DEFAULT_BYPASS_LEVELS,
) -> list[DayGroup]:
    """What the family sees: visible occurrences only (spec 3.9)."""
    since, _ = local_day_bounds(now.date() - timedelta(days=days - 1), now)
    rows = visible_symptoms(session, elder_id, since=since, bypass_levels=bypass_levels)
    return group_by_day(rows, now, days)
