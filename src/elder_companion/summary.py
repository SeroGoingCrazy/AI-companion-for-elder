"""Today's summary for the family dashboard (spec 5.4): today's chat + symptoms + alerts -> LLM.

Only what the family may see goes in (spec 3.9): private messages are left out, and a day with
a private segment ends with a fixed note added in code, never by the model.

Results are cached per elder and day. A cached summary is reused while the underlying data is
unchanged (and for at most `ttl`), so the demo's "refresh" right after a new symptom does not
show a stale summary, while repeated dashboard loads cost no API calls.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from elder_companion.chat.context import to_local
from elder_companion.dashboard import local_day_bounds
from elder_companion.db import utcnow
from elder_companion.elders import get_elder
from elder_companion.llm import BaseLLMClient, LLMError
from elder_companion.models import Alert, Elder, Message
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

EMPTY_SUMMARY = "No conversations yet today."
MAX_MESSAGES = 60
MAX_MESSAGE_CHARS = 300
MAX_SUMMARY_TOKENS = 220
DEFAULT_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class SummaryResult:
    summary: str
    generated_at: datetime  # naive UTC
    fallback: bool = False  # True when the LLM failed and a plain rule-based summary was used
    empty: bool = False  # True when she has not chatted today
    has_private: bool = False  # part of today's conversation was private (the note is added)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_MESSAGE_CHARS else text[:MAX_MESSAGE_CHARS] + "…"


def _hhmm(ts: datetime, now: datetime) -> str:
    local = to_local(ts, now)
    return f"{local.hour % 12 or 12}:{local:%M} {local:%p}"


def format_notes(
    messages: Sequence[Message],
    symptoms: Sequence[SymptomView],
    alerts: Sequence[Alert],
    now: datetime,
    *,
    companion_name: str,
) -> str:
    """The user message: today's transcript, symptom log and alerts, with local times."""
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
        f"Today is {now:%A, %B} {now.day}.\n\n"
        "Conversation today:\n" + "\n".join(transcript) + "\n\n"
        "Symptom log today:\n" + ("\n".join(symptom_lines) or "(none)") + "\n\n"
        "Alerts today:\n" + ("\n".join(alert_lines) or "(none)")
    )


def fallback_summary(
    elder: Elder,
    messages: Sequence[Message],
    symptoms: Sequence[SymptomView],
    alerts: Sequence[Alert],
) -> str:
    turns = sum(m.role == "user" for m in messages)
    parts = [f"{elder.nickname} chatted {turns} time{'s' if turns != 1 else ''} today."]
    if symptoms:
        parts.append("She mentioned: " + ", ".join(s.label for s in symptoms) + ".")
    if alerts:
        parts.append("Alerts: " + ", ".join(f"{a.title} ({a.level})" for a in alerts) + ".")
    return " ".join(parts)


def with_note(summary: str, note: str) -> str:
    return f"{summary} {note}" if note else summary


class DailySummary:
    def __init__(
        self,
        llm: BaseLLMClient,
        *,
        companion_name: str,
        ttl: timedelta = DEFAULT_TTL,
        clock: Callable[[], datetime] = utcnow,
        bypass_levels: Collection[str] = DEFAULT_BYPASS_LEVELS,
    ) -> None:
        self._llm = llm
        self._bypass = tuple(bypass_levels)
        self._companion_name = companion_name
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[tuple[int, date], tuple[tuple, SummaryResult]] = {}
        self._lock = threading.Lock()

    def get(
        self, session: Session, elder_id: int | None, now: datetime, *, refresh: bool = False
    ) -> SummaryResult:
        """Summary of the elder's local day containing `now` (an aware datetime in her zone)."""
        elder = get_elder(session, elder_id)
        day = now.date()
        start, end = local_day_bounds(day, now)
        messages, private_ids = visible_messages(session, elder.id, start, end)
        note = PRIVATE_DAY_NOTE.format(nickname=elder.nickname) if private_ids else ""
        if not any(m.role == "user" for m in messages):
            if note:  # she only talked privately today: say so, and nothing else
                return SummaryResult(note, self._clock(), has_private=True)
            return SummaryResult(EMPTY_SUMMARY, self._clock(), empty=True)
        symptoms = visible_symptoms(
            session, elder.id, since=start, until=end, bypass_levels=self._bypass
        )
        alerts = sorted(
            visible_alerts(
                session, elder.id, limit=100, since=start, until=end, bypass_levels=self._bypass
            ),
            key=lambda a: (a.created_at, a.id),
        )

        key = (elder.id, day)
        # Private ids are part of the key: marking something private after a summary was
        # generated (the request often comes after the content) regenerates it.
        fingerprint = (*self._fingerprint(messages, symptoms, alerts), private_ids)
        with self._lock:
            hit = self._cache.get(key)
        if (
            hit
            and not refresh
            and hit[0] == fingerprint
            and self._clock() - hit[1].generated_at < self._ttl
        ):
            return hit[1]

        notes = format_notes(messages, symptoms, alerts, now, companion_name=self._companion_name)
        system = render_prompt(
            "daily_summary",
            name=elder.name,
            nickname=elder.nickname,
            profile=elder.profile_text.strip() or "(no background on file)",
        )
        try:
            text = self._llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": notes}],
                max_tokens=MAX_SUMMARY_TOKENS,
            )
        except LLMError:
            logger.warning("daily summary LLM call failed; using a plain summary", exc_info=True)
            text = with_note(fallback_summary(elder, messages, symptoms, alerts), note)
            # not cached: retry next time
            return SummaryResult(text, self._clock(), fallback=True, has_private=bool(note))
        result = SummaryResult(with_note(text.strip(), note), self._clock(), has_private=bool(note))
        with self._lock:
            self._cache[key] = (fingerprint, result)
        return result

    @staticmethod
    def _fingerprint(messages, symptoms, alerts) -> tuple:  # noqa: ANN001
        return (
            messages[-1].id,
            tuple((s.id, s.count, s.status, s.severity) for s in symptoms),
            tuple(a.id for a in alerts),
        )
