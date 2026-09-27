"""One-pager for the doctor (spec 2.8, 3.10): the symptom log of the last N days.

Rendered from structured data by a template, with no LLM anywhere (ADR 21): every quote is a
stored raw_quote with its timestamp, so nothing can be invented. Reads through `privacy`.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from elder_companion.dashboard import local_day_bounds
from elder_companion.models import Alert, Elder
from elder_companion.privacy import (
    DEFAULT_BYPASS_LEVELS,
    SymptomView,
    visible_alerts,
    visible_symptoms,
)
from elder_companion.symptoms.merge import max_severity
from elder_companion.symptoms.schema import SymptomCatalog, get_catalog

MAX_QUOTES = 4  # per symptom, most recent; keeps the page to <= 2 Letter pages
DISCLAIMER = "Prepared from conversations with an AI companion; not a clinical record."


@dataclass(frozen=True)
class Quote:
    at: datetime  # the elder's local time
    text: str


@dataclass
class SymptomSummary:
    name: str  # English display name ("Knee pain" for `other` labels)
    name_zh: str
    red_flag: bool
    first_seen: datetime  # local
    last_seen: datetime  # local
    count: int = 0
    max_severity: str = "unknown"
    status: str = "new"  # of the latest mention
    quotes: list[Quote] = field(default_factory=list)  # newest first, at most MAX_QUOTES
    total_quotes: int = 0


@dataclass(frozen=True)
class AlertLine:
    at: datetime  # local
    type: str
    title: str


@dataclass(frozen=True)
class DoctorReport:
    elder: Elder
    days: int
    start: datetime  # local, first day 00:00
    end: datetime  # local, now
    symptoms: list[SymptomSummary]
    red_flags: list[AlertLine]
    disclaimer: str = DISCLAIMER


def _local(ts: datetime, now: datetime) -> datetime:
    return ts.replace(tzinfo=UTC).astimezone(now.tzinfo)


def summarize(
    views: Sequence[SymptomView], now: datetime, catalog: SymptomCatalog
) -> list[SymptomSummary]:
    """One line per symptom (log rows of the same canonical are combined; `other` by label).
    Red flags first, then the most frequent."""
    by_key: dict[tuple[str, str], SymptomSummary] = {}
    for v in sorted(views, key=lambda v: (v.last_seen, v.id)):
        d = catalog.get(v.canonical) if v.canonical in catalog else catalog.get("other")
        key = (v.canonical, v.label.casefold() if v.canonical == "other" else "")
        s = by_key.get(key)
        if s is None:
            s = by_key[key] = SymptomSummary(
                name=v.label if v.canonical == "other" else d.en,
                name_zh="" if v.canonical == "other" else d.zh,
                red_flag=v.red_flag,
                first_seen=_local(v.first_seen, now),
                last_seen=_local(v.last_seen, now),
            )
        s.count += v.count
        s.max_severity = max_severity(s.max_severity, v.severity)
        s.first_seen = min(s.first_seen, _local(v.first_seen, now))
        s.last_seen = max(s.last_seen, _local(v.last_seen, now))
        s.status = v.status
        mentions = v.mentions or ()
        quotes = (
            [Quote(_local(m.created_at, now), m.raw_quote) for m in mentions]
            if mentions
            else [Quote(_local(v.last_seen, now), v.raw_quote)]  # logged before mentions existed
        )
        s.quotes.extend(quotes)
    out = list(by_key.values())
    for s in out:
        s.quotes.sort(key=lambda q: q.at, reverse=True)
        s.total_quotes = len(s.quotes)
        s.quotes = s.quotes[:MAX_QUOTES]
    return sorted(out, key=lambda s: (not s.red_flag, -s.count, s.name))


def red_flag_lines(alerts: Sequence[Alert], now: datetime) -> list[AlertLine]:
    return [
        AlertLine(_local(a.created_at, now), a.type, a.title)
        for a in sorted(alerts, key=lambda a: (a.created_at, a.id))
        if a.level == "high"
    ]


def build_doctor_report(
    session: Session,
    elder: Elder,
    now: datetime,
    days: int = 30,
    *,
    catalog: SymptomCatalog | None = None,
    bypass_levels: Collection[str] = DEFAULT_BYPASS_LEVELS,
) -> DoctorReport:
    """`now` is aware, in the elder's zone; the window is the last `days` calendar days."""
    catalog = catalog or get_catalog()
    first_day = now.date() - timedelta(days=days - 1)
    since, _ = local_day_bounds(first_day, now)
    views = visible_symptoms(
        session, elder.id, since=since, catalog=catalog, bypass_levels=bypass_levels
    )
    alerts = visible_alerts(session, elder.id, limit=500, since=since, bypass_levels=bypass_levels)
    return DoctorReport(
        elder=elder,
        days=days,
        start=_local(since, now),
        end=now,
        symptoms=summarize(views, now, catalog),
        red_flags=red_flag_lines(alerts, now),
    )
