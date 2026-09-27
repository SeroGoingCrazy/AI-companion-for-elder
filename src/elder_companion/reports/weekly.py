"""The weekly report (spec 3.10, H7): seven days of digests, symptoms and adherence.

Everything countable is computed here, in code: the mood line, how often each topic came up,
how many days each symptom appeared and whether it is heading the right way, and how she did
with her reminders. The model only writes the short overview at the top, from these same
numbers, so nothing on the page can be invented.

Built from visible data only (spec 3.9). A day she asked to keep private is shown as a gap
with a note, never with its content.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from elder_companion.dashboard import local_day_bounds
from elder_companion.db import utcnow
from elder_companion.family_loop.reminders import ReminderView, list_with_adherence
from elder_companion.llm import BaseLLMClient, LLMError
from elder_companion.models import Elder
from elder_companion.privacy import DEFAULT_BYPASS_LEVELS, SymptomView, visible_symptoms
from elder_companion.prompts import render_prompt
from elder_companion.reports.digest import DigestService, DigestView, stored_digests
from elder_companion.symptoms.schema import SEVERITY_ORDER, SymptomCatalog, get_catalog

logger = logging.getLogger(__name__)

WEEK_DAYS = 7
MAX_TOPICS_SHOWN = 5
MAX_OVERVIEW_TOKENS = 200
NO_OVERVIEW = "Here is how the week went."

Trend = str  # "better" | "worse" | "same" | "new"


# ---------- pure aggregation ----------


@dataclass(frozen=True)
class MoodPoint:
    date: date
    label: str  # "Mon"
    score: int | None  # None = no chat that day
    has_private: bool


@dataclass(frozen=True)
class TopicCount:
    topic: str
    days: int


@dataclass(frozen=True)
class SymptomStat:
    canonical: str
    label: str
    display: str
    red_flag: bool
    days: int  # distinct days it came up
    mentions: int
    max_severity: str
    trend: Trend
    last_seen: datetime


def week_days(end: date, days: int = WEEK_DAYS) -> list[date]:
    """The `days` local dates ending on `end`, oldest first."""
    return [end - timedelta(days=i) for i in range(days - 1, -1, -1)]


def mood_line(digests: Sequence[DigestView], days: Sequence[date]) -> list[MoodPoint]:
    by_date = {d.date: d for d in digests}
    points = []
    for day in days:
        d = by_date.get(day)
        points.append(
            MoodPoint(
                date=day,
                label=f"{day:%a}",
                score=d.mood_score if d else None,
                has_private=bool(d and d.has_private),
            )
        )
    return points


def average_mood(points: Sequence[MoodPoint]) -> float | None:
    scores = [p.score for p in points if p.score is not None]
    return round(sum(scores) / len(scores), 1) if scores else None


def mood_direction(points: Sequence[MoodPoint]) -> Trend:
    """Compares the first half of the week with the second. Needs a score on both sides."""
    scored = [p for p in points if p.score is not None]
    if len(scored) < 2:
        return "same"
    half = len(scored) // 2
    first = scored[:half]
    second = scored[len(scored) - half :]
    delta = (sum(p.score for p in second) / len(second)) - (
        sum(p.score for p in first) / len(first)
    )
    if delta >= 0.5:
        return "better"
    if delta <= -0.5:
        return "worse"
    return "same"


def topic_counts(digests: Sequence[DigestView], limit: int = MAX_TOPICS_SHOWN) -> list[TopicCount]:
    """How many days each topic came up, most days first, then alphabetically."""
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for d in digests:
        for topic in set(t.casefold() for t in d.topics):
            counts[topic] = counts.get(topic, 0) + 1
    for d in digests:
        for topic in d.topics:
            labels.setdefault(topic.casefold(), topic)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [TopicCount(labels[t], n) for t, n in ordered[:limit]]


def severity_trend(symptom: SymptomView) -> Trend:
    """From her own words over the week: did it get lighter, heavier, or stay put?

    `status` is what she said about it last ("improved"/"resolved" are her judgement, and
    outrank the severity arithmetic). Otherwise compare the first and last visible mention.
    """
    if symptom.status in ("improved", "resolved"):
        return "better"
    mentions = symptom.mentions
    if len(mentions) < 2:
        return "new" if symptom.count <= 1 else "same"
    first = SEVERITY_ORDER.index(_known(mentions[0].severity))
    last = SEVERITY_ORDER.index(_known(mentions[-1].severity))
    if last > first:
        return "worse"
    if last < first:
        return "better"
    return "same"


def _known(severity: str) -> str:
    return severity if severity in SEVERITY_ORDER else "unknown"


def max_severity_of(symptom: SymptomView) -> str:
    seen = [_known(m.severity) for m in symptom.mentions] or [_known(symptom.severity)]
    return max(seen, key=SEVERITY_ORDER.index)


def group_key(symptom: SymptomView) -> tuple[str, str]:
    """One line per symptom. `other` has no shared canonical, so its label separates it."""
    return symptom.canonical, symptom.label.casefold() if symptom.canonical == "other" else ""


def combined_trend(group: Sequence[SymptomView]) -> Trend:
    """Across every log row for this symptom, in the order she said them."""
    if any(s.status in ("improved", "resolved") for s in group):
        if not any(s.status in ("new", "ongoing") for s in group):
            return "better"
    mentions = sorted(
        (m for s in group for m in s.mentions), key=lambda m: (m.created_at, m.message_id or 0)
    )
    if len(mentions) < 2:
        return "new" if sum(s.count for s in group) <= 1 else "same"
    first = SEVERITY_ORDER.index(_known(mentions[0].severity))
    last = SEVERITY_ORDER.index(_known(mentions[-1].severity))
    if last > first:
        return "worse"
    if last < first:
        return "better"
    return "same"


def symptom_stats(
    symptoms: Sequence[SymptomView], catalog: SymptomCatalog | None = None
) -> list[SymptomStat]:
    """One line per symptom, red flags first, then the ones she brought up on the most days.

    Several `symptom_log` rows can describe the same thing across a week (a new row is
    started once the merge window closes), so they are combined here the way the doctor
    one-pager does it. Four separate "Joint pain" lines would read as four problems.
    """
    catalog = catalog or get_catalog()
    groups: dict[tuple[str, str], list[SymptomView]] = {}
    for s in symptoms:
        groups.setdefault(group_key(s), []).append(s)

    stats = []
    for (canonical, _), group in groups.items():
        d = catalog.get(canonical) if canonical in catalog else catalog.get("other")
        latest = max(group, key=lambda s: s.last_seen)
        days = len({m.created_at.date() for s in group for m in s.mentions})
        stats.append(
            SymptomStat(
                canonical=canonical,
                label=latest.label,
                display=latest.label if canonical == "other" else d.en,
                red_flag=any(s.red_flag for s in group),
                days=days or len(group),
                mentions=sum(s.count for s in group),
                max_severity=max((max_severity_of(s) for s in group), key=SEVERITY_ORDER.index),
                trend=combined_trend(group),
                last_seen=latest.last_seen,
            )
        )
    return sorted(stats, key=lambda s: (not s.red_flag, -s.days, -s.mentions, s.display))


# ---------- the assembled report ----------


@dataclass(frozen=True)
class WeeklyReportView:
    start: date
    end: date
    overview: str
    moods: tuple[MoodPoint, ...]
    average_mood: float | None
    mood_direction: Trend
    topics: tuple[TopicCount, ...]
    symptoms: tuple[SymptomStat, ...]
    reminders: tuple[ReminderView, ...]
    chat_days: int
    private_days: int
    generated_at: datetime  # naive UTC
    generated_on: date  # her local date, for the footer
    fallback: bool = False  # the overview is the plain one; the numbers are always real

    @property
    def has_data(self) -> bool:
        return self.chat_days > 0


def format_facts(report: WeeklyReportView, nickname: str) -> str:
    """What the model is given: only the numbers already computed above."""
    mood = " ".join(f"{p.label}={p.score if p.score is not None else '-'}" for p in report.moods)
    topics = ", ".join(f"{t.topic} ({t.days}d)" for t in report.topics) or "(none)"
    symptoms = (
        "\n".join(
            f"- {s.display}: {s.days} day(s), {s.mentions} mention(s), "
            f"worst {s.max_severity}, {s.trend}" + (" [emergency sign]" if s.red_flag else "")
            for s in report.symptoms
        )
        or "(none)"
    )
    reminders = (
        "\n".join(
            f"- {r.text}: did it on {r.confirmed_days} of {r.raised_days} day(s) asked"
            for r in report.reminders
            if r.raised_days
        )
        or "(none)"
    )
    return (
        f"Week of {report.start:%B} {report.start.day} to {report.end:%B} {report.end.day}.\n"
        f"{nickname} chatted on {report.chat_days} of {len(report.moods)} days.\n\n"
        f"Mood 1-5 by day: {mood}\n"
        f"Average mood: {report.average_mood if report.average_mood is not None else 'n/a'} "
        f"({report.mood_direction} across the week)\n\n"
        f"Topics she talked about: {topics}\n\n"
        f"Symptoms:\n{symptoms}\n\n"
        f"Reminders:\n{reminders}\n\n"
        f"Days with something she asked to keep private: {report.private_days}"
    )


def fallback_overview(report: WeeklyReportView, nickname: str) -> str:
    parts = [f"{nickname} chatted on {report.chat_days} of {len(report.moods)} days."]
    if report.average_mood is not None:
        parts.append(f"Her mood averaged {report.average_mood} out of 5 ({report.mood_direction}).")
    if report.symptoms:
        parts.append("She mentioned " + ", ".join(s.display for s in report.symptoms[:3]) + ".")
    return " ".join(parts)


class WeeklyReport:
    def __init__(
        self,
        llm: BaseLLMClient,
        *,
        companion_name: str,
        bypass_levels: Collection[str] = DEFAULT_BYPASS_LEVELS,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._llm = llm
        self._digests = DigestService(
            llm, companion_name=companion_name, bypass_levels=bypass_levels, clock=clock
        )
        self._bypass = tuple(bypass_levels)
        self._clock = clock

    def get(
        self, session: Session, elder: Elder, now: datetime, *, weeks_back: int = 0
    ) -> WeeklyReportView:
        """The 7 local days ending `weeks_back` weeks before today. `now` is aware, in her zone."""
        end = now.date() - timedelta(days=WEEK_DAYS * max(0, weeks_back))
        days = week_days(end)
        digests = self._week_digests(session, elder, days, now)
        moods = mood_line(digests, days)

        start_utc, _ = _bounds(days[0], now)
        _, end_utc = _bounds(days[-1], now)
        symptoms = visible_symptoms(
            session, elder.id, since=start_utc, until=end_utc, bypass_levels=self._bypass
        )
        report = WeeklyReportView(
            start=days[0],
            end=days[-1],
            overview="",
            moods=tuple(moods),
            average_mood=average_mood(moods),
            mood_direction=mood_direction(moods),
            topics=tuple(topic_counts(digests)),
            symptoms=tuple(symptom_stats(symptoms)),
            reminders=tuple(list_with_adherence(session, elder.id, end, days=WEEK_DAYS)),
            chat_days=sum(not d.empty for d in digests),
            private_days=sum(d.has_private for d in digests),
            generated_at=self._clock(),
            generated_on=now.date(),
        )
        if not report.has_data:
            return _with(report, overview=f"{elder.nickname} has not chatted this week yet.")
        return self._add_overview(report, elder)

    def _week_digests(
        self, session: Session, elder: Elder, days: Sequence[date], now: datetime
    ) -> list[DigestView]:
        """A past day that already has a digest is reused as it is: it cannot change by
        itself, and a privacy request drops the days it touches (`digest.invalidate`).
        Today, and any day never summarized, is built now.
        """
        stored = stored_digests(session, elder.id, days)
        today = now.date()
        return [
            stored[day]
            if day in stored and day != today
            else self._digests.get(session, elder, day, now)
            for day in days
        ]

    def _add_overview(self, report: WeeklyReportView, elder: Elder) -> WeeklyReportView:
        system = render_prompt(
            "weekly_report",
            name=elder.name,
            nickname=elder.nickname,
            profile=elder.profile_text.strip() or "(no background on file)",
        )
        facts = format_facts(report, elder.nickname)
        try:
            text = self._llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": facts}],
                max_tokens=MAX_OVERVIEW_TOKENS,
            )
        except LLMError:
            logger.warning("weekly overview failed; using a plain one", exc_info=True)
            return _with(report, overview=fallback_overview(report, elder.nickname), fallback=True)
        return _with(report, overview=text.strip() or NO_OVERVIEW)


def _with(report: WeeklyReportView, **changes: object) -> WeeklyReportView:
    return replace(report, **changes)


def _bounds(day: date, now: datetime) -> tuple[datetime, datetime]:
    return local_day_bounds(day, now)
