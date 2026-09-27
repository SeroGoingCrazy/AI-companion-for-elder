"""One day of her life, summarized once and stored (spec 3.10, H7).

The dashboard's "Today" card and the weekly report both read digests, so a day costs one
model call however often it is looked at. Only what the family may see goes in (spec 3.9):
private messages are left out, and a day that held something back carries a fixed note added
in code, never by the model.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.chat.context import to_local
from elder_companion.dashboard import local_day_bounds
from elder_companion.db import utcnow
from elder_companion.llm import BaseLLMClient, LLMError
from elder_companion.models import Alert, DailyDigest, Elder, Message
from elder_companion.privacy import (
    DEFAULT_BYPASS_LEVELS,
    PRIVATE_DAY_NOTE,
    SymptomView,
    visible_alerts,
    visible_messages,
    visible_symptoms,
)
from elder_companion.prompts import render_prompt

logger = logging.getLogger(__name__)

SCHEMA_NAME = "daily_digest"
MAX_DIGEST_TOKENS = 260
MAX_TOPICS = 4
EMPTY_SUMMARY = "No conversations yet today."
NEUTRAL_MOOD = 3


class DigestOut(BaseModel):
    """What the model returns for one day."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="At most 3 plain sentences for the family")
    mood_score: int = Field(description="1 = very low, 3 = ordinary, 5 = bright and cheerful")
    topics: list[str] = Field(description="Up to 4 short topic labels, e.g. 'garden', 'Leo'")


def digest_schema() -> dict[str, Any]:
    return DigestOut.model_json_schema()


@dataclass(frozen=True)
class DigestView:
    """A stored digest, as the rest of the app reads it."""

    date: date
    summary: str
    mood_score: int | None
    topics: tuple[str, ...]
    has_private: bool
    generated_at: datetime
    empty: bool = False  # she did not chat that day
    fallback: bool = False  # the model failed; this is a plain rule-based summary

    @classmethod
    def of(cls, row: DailyDigest) -> DigestView:
        return cls(
            date=row.date,
            summary=row.summary,
            mood_score=row.mood_score,
            topics=tuple(json.loads(row.topics_json or "[]")),
            has_private=row.has_private,
            generated_at=row.generated_at,
            empty=row.mood_score is None and not row.topics_json.strip("[] "),
        )


def normalize_topics(topics: Sequence[str]) -> tuple[str, ...]:
    """Trim, drop blanks, de-duplicate case-insensitively, keep at most MAX_TOPICS."""
    seen: dict[str, str] = {}
    for raw in topics:
        label = " ".join(str(raw).split())
        if label and label.casefold() not in seen:
            seen[label.casefold()] = label
    return tuple(seen.values())[:MAX_TOPICS]


def clamp_mood(score: int) -> int:
    return max(1, min(5, int(score)))


def with_note(summary: str, note: str) -> str:
    return f"{summary} {note}" if note else summary


def fingerprint(
    messages: Sequence[Message],
    symptoms: Sequence[SymptomView],
    alerts: Sequence[Alert],
    private_ids: Sequence[int],
) -> str:
    """Changes when anything the digest is built from changes, including a later privacy
    request (which usually arrives after the content it covers)."""
    parts = [
        str(messages[-1].id if messages else 0),
        ",".join(f"{s.id}:{s.count}:{s.status}:{s.severity}" for s in symptoms),
        ",".join(str(a.id) for a in alerts),
        ",".join(str(i) for i in private_ids),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


MAX_MESSAGES = 60
MAX_MESSAGE_CHARS = 300


def _clip(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE_CHARS else text[:MAX_MESSAGE_CHARS] + "…"


def _hhmm(ts: datetime, now: datetime) -> str:
    local = to_local(ts, now)
    return f"{local.hour % 12 or 12}:{local:%M} {local:%p}"


def format_notes(
    messages: Sequence[Message],
    symptoms: Sequence[SymptomView],
    alerts: Sequence[Alert],
    day: date,
    now: datetime,
    *,
    companion_name: str,
) -> str:
    """The user message: that day's transcript, symptom log and alerts, with local times."""
    transcript = [
        f"[{_hhmm(m.created_at, now)}] {'Elder' if m.role == 'user' else companion_name}: "
        f"{_clip(m.text)}"
        for m in list(messages)[-MAX_MESSAGES:]
    ]
    symptom_lines = []
    for s in symptoms:
        details = [
            f"severity {s.severity}" if s.severity != "unknown" else None,
            f"for {s.duration}" if s.duration else None,
            f"status {s.status}",
            f"mentioned {s.count}x" if s.count > 1 else None,
        ]
        symptom_lines.append(
            f"- {s.canonical}: {s.label} ({', '.join(d for d in details if d)}); "
            f'she said "{s.raw_quote}"'
        )
    alert_lines = [
        f"- [{_hhmm(a.created_at, now)}] {a.level}: {a.title}. {a.content}" for a in alerts
    ]
    return (
        f"The day is {day:%A, %B} {day.day}.\n\n"
        "Conversation that day:\n" + "\n".join(transcript) + "\n\n"
        "Symptom log that day:\n" + ("\n".join(symptom_lines) or "(none)") + "\n\n"
        "Alerts that day:\n" + ("\n".join(alert_lines) or "(none)")
    )


def fallback_digest(
    nickname: str, messages: Sequence[Message], symptoms: Sequence[SymptomView]
) -> str:
    turns = sum(m.role == "user" for m in messages)
    parts = [f"{nickname} chatted {turns} time{'s' if turns != 1 else ''} that day."]
    if symptoms:
        parts.append("She mentioned: " + ", ".join(s.label for s in symptoms) + ".")
    return " ".join(parts)


@dataclass(frozen=True)
class DayData:
    """Everything visible about one day, gathered before any model call."""

    messages: tuple[Message, ...]
    symptoms: tuple[SymptomView, ...]
    alerts: tuple[Alert, ...]
    private_ids: tuple[int, ...]

    @property
    def has_chat(self) -> bool:
        return any(m.role == "user" for m in self.messages)


class DigestService:
    """Builds and stores one `daily_digest` row per elder-local day."""

    def __init__(
        self,
        llm: BaseLLMClient,
        *,
        companion_name: str,
        bypass_levels: Collection[str] = DEFAULT_BYPASS_LEVELS,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._llm = llm
        self._companion_name = companion_name
        self._bypass = tuple(bypass_levels)
        self._clock = clock

    def gather(self, session: Session, elder_id: int, day: date, now: datetime) -> DayData:
        """`now` is aware, in her zone; it supplies the zone, not the day."""
        start, end = local_day_bounds(day, now)
        messages, private_ids = visible_messages(session, elder_id, start, end)
        return DayData(
            messages=tuple(messages),
            symptoms=tuple(
                visible_symptoms(
                    session, elder_id, since=start, until=end, bypass_levels=self._bypass
                )
            ),
            alerts=tuple(
                sorted(
                    visible_alerts(
                        session,
                        elder_id,
                        limit=100,
                        since=start,
                        until=end,
                        bypass_levels=self._bypass,
                    ),
                    key=lambda a: (a.created_at, a.id),
                )
            ),
            private_ids=private_ids,
        )

    def get(
        self,
        session: Session,
        elder: Elder,
        day: date,
        now: datetime,
        *,
        refresh: bool = False,
    ) -> DigestView:
        """The digest for `day`, generating and storing it if it is missing or out of date."""
        data = self.gather(session, elder.id, day, now)
        note = PRIVATE_DAY_NOTE.format(nickname=elder.nickname) if data.private_ids else ""
        row = session.scalars(
            select(DailyDigest).where(DailyDigest.elder_id == elder.id, DailyDigest.date == day)
        ).first()

        if not data.has_chat:
            # Nothing to summarize. A day she only spoke privately still says so.
            view = DigestView(
                date=day,
                summary=note or EMPTY_SUMMARY,
                mood_score=None,
                topics=(),
                has_private=bool(note),
                generated_at=self._clock(),
                empty=not note,
            )
            if row is not None:
                session.delete(row)
                session.commit()
            return view

        mark = fingerprint(data.messages, data.symptoms, data.alerts, data.private_ids)
        if row is not None and row.fingerprint == mark and not refresh:
            return DigestView.of(row)

        try:
            out = self._generate(elder, data, day, now)
        except LLMError:
            logger.warning("digest failed for %s; using a plain summary", day, exc_info=True)
            # Not stored: a fallback must not be mistaken for a real digest later.
            return DigestView(
                date=day,
                summary=with_note(
                    fallback_digest(elder.nickname, data.messages, data.symptoms), note
                ),
                mood_score=None,
                topics=(),
                has_private=bool(note),
                generated_at=self._clock(),
                fallback=True,
            )

        values = {
            "summary": with_note(out.summary.strip(), note),
            "mood_score": clamp_mood(out.mood_score),
            "topics_json": json.dumps(list(normalize_topics(out.topics))),
            "has_private": bool(note),
            "fingerprint": mark,
            "generated_at": self._clock(),
        }
        if row is None:
            row = DailyDigest(elder_id=elder.id, date=day, **values)
            session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        session.commit()
        return DigestView.of(row)

    def _generate(self, elder: Elder, data: DayData, day: date, now: datetime) -> DigestOut:
        notes = format_notes(
            data.messages,
            data.symptoms,
            data.alerts,
            day,
            now,
            companion_name=self._companion_name,
        )
        system = render_prompt(
            "daily_summary",
            name=elder.name,
            nickname=elder.nickname,
            profile=elder.profile_text.strip() or "(no background on file)",
        )
        raw = self._llm.extract_json(
            [{"role": "system", "content": system}, {"role": "user", "content": notes}],
            schema=digest_schema(),
            name=SCHEMA_NAME,
        )
        try:
            return DigestOut.model_validate(raw)
        except ValidationError as e:
            raise LLMError(f"digest did not match the schema: {e}") from e


def invalidate(session: Session, elder_id: int, days: Collection[date]) -> int:
    """Drop stored digests for `days` so they are rebuilt (spec 5.4). Called when a privacy
    request lands, which usually arrives after the content it covers: the day it touches was
    very likely already summarized with that content in it."""
    if not days:
        return 0
    rows = list(
        session.scalars(
            select(DailyDigest).where(
                DailyDigest.elder_id == elder_id, DailyDigest.date.in_(list(days))
            )
        )
    )
    for row in rows:
        session.delete(row)
    return len(rows)


def stored_digests(session: Session, elder_id: int, days: Sequence[date]) -> dict[date, DigestView]:
    """Digests already on record for `days` (no model calls)."""
    if not days:
        return {}
    rows = session.scalars(
        select(DailyDigest).where(
            DailyDigest.elder_id == elder_id, DailyDigest.date.in_(list(days))
        )
    )
    return {row.date: DigestView.of(row) for row in rows}
