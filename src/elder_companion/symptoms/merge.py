"""Merge a freshly extracted symptom into the log (spec 3.4.3). Pure: the caller does the DB work.

Timestamps are naive UTC, like everything stored in the DB.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from elder_companion.models import SymptomLog
from elder_companion.symptoms.schema import SEVERITY_ORDER, Severity, SymptomItem

_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}


def max_severity(a: str, b: str) -> Severity:
    return max(a, b, key=lambda s: _RANK.get(s, 0))  # type: ignore[return-value]


def same_symptom(existing: SymptomLog, item: SymptomItem) -> bool:
    """Same canonical; `other` entries additionally need the same label (case-insensitive)."""
    if existing.canonical != item.canonical:
        return False
    if item.canonical != "other":
        return True
    return existing.label.strip().casefold() == item.label.strip().casefold()


def find_match(
    candidates: Iterable[SymptomLog], item: SymptomItem, now: datetime, window: timedelta
) -> SymptomLog | None:
    """Most recently seen log row for the same symptom within `window`, if any."""
    matches = [c for c in candidates if same_symptom(c, item) and now - c.last_seen <= window]
    return max(matches, key=lambda c: c.last_seen, default=None)


@dataclass(frozen=True)
class MergeResult:
    is_new: bool
    values: dict[str, Any]  # column -> value, to insert (is_new) or to set on `existing`


def merge_symptom(
    existing: SymptomLog | None,
    item: SymptomItem,
    now: datetime,
    *,
    message_id: int | None = None,
    window: timedelta = timedelta(hours=24),
) -> MergeResult:
    if existing is None or not same_symptom(existing, item) or now - existing.last_seen > window:
        return MergeResult(
            is_new=True,
            values={
                **item.model_dump(),
                "message_id": message_id,
                "count": 1,
                "first_seen": now,
                "last_seen": now,
            },
        )
    return MergeResult(
        is_new=False,
        values={
            "count": existing.count + 1,
            "last_seen": now,
            "severity": max_severity(existing.severity, item.severity),
            # Mentioning it again within the window means it is still going on.
            "status": "ongoing" if item.status == "new" else item.status,
            "raw_quote": item.raw_quote,  # the dashboard shows her latest words
            "message_id": message_id,
            # Keep details she gave earlier unless she says something new.
            "body_part": item.body_part or existing.body_part,
            "duration": item.duration or existing.duration,
            "onset": item.onset or existing.onset,
        },
    )
