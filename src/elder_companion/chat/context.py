"""Build the message list sent to the chat model. Pure functions: callers do the DB queries."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from elder_companion.llm import ChatMessage
from elder_companion.models import Elder, Message, SymptomLog
from elder_companion.prompts import render_prompt

_CJK = re.compile(r"[一-鿿]")
LANGUAGE_NAMES = {"en": "English", "zh": "Chinese"}
NO_FOLLOW_UPS = "(nothing in particular)"


def detect_language(text: str) -> str:
    return "zh" if _CJK.search(text) else "en"


def part_of_day(now: datetime) -> str:
    h = now.hour
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


def format_now(now: datetime) -> str:
    """e.g. 'Tuesday, September 29, 2026, 8:05 AM' (no platform-specific strftime flags)."""
    hour12 = now.hour % 12 or 12
    return f"{now:%A, %B} {now.day}, {now.year}, {hour12}:{now:%M} {now:%p}"


def to_local(ts_utc_naive: datetime, now: datetime) -> datetime:
    """DB timestamps are naive UTC; convert to the elder's zone (taken from `now`)."""
    return ts_utc_naive.replace(tzinfo=UTC).astimezone(now.tzinfo)


def relative_day(ts: datetime, now: datetime) -> str:
    days = (now.date() - ts.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def format_follow_up(s: SymptomLog, now: datetime) -> str:
    details = [d for d in (s.body_part, s.severity if s.severity != "unknown" else None) if d]
    line = f"- {s.label}"
    if details:
        line += f" ({', '.join(details)})"
    line += f", last mentioned {relative_day(to_local(s.last_seen, now), now)}"
    if s.count > 1:
        line += f", {s.count} times so far"
    if s.status == "improved":
        line += "; she said it was getting better"
    return line


def follow_up_candidates(symptoms: Sequence[SymptomLog]) -> list[SymptomLog]:
    return [s for s in symptoms if s.status != "resolved"]


def system_prompt(
    elder: Elder, symptoms: Sequence[SymptomLog], now: datetime, *, companion_name: str
) -> str:
    follow_ups = [format_follow_up(s, now) for s in follow_up_candidates(symptoms)]
    return render_prompt(
        "companion",
        companion_name=companion_name,
        name=elder.name,
        nickname=elder.nickname,
        profile=elder.profile_text.strip() or "(no background on file)",
        now=format_now(now),
        part_of_day=part_of_day(now),
        follow_ups="\n".join(follow_ups) or NO_FOLLOW_UPS,
    )


def history_messages(history: Sequence[Message], history_turns: int) -> list[ChatMessage]:
    """Keep the last `history_turns` user/assistant pairs, oldest first."""
    kept = list(history)[-2 * history_turns :] if history_turns > 0 else []
    return [{"role": m.role, "content": m.text} for m in kept]


def build_context(
    elder: Elder,
    history: Sequence[Message],
    symptoms: Sequence[SymptomLog],
    now: datetime,
    *,
    companion_name: str,
    history_turns: int,
) -> list[ChatMessage]:
    """System prompt + recent history. `history` must already include the current user message."""
    system = system_prompt(elder, symptoms, now, companion_name=companion_name)
    return [{"role": "system", "content": system}, *history_messages(history, history_turns)]


def greeting_language(elder: Elder, history: Sequence[Message]) -> str:
    last_user = next((m for m in reversed(history) if m.role == "user"), None)
    return detect_language(last_user.text) if last_user else (elder.language or "en")


def build_greet_context(
    elder: Elder,
    history: Sequence[Message],
    symptoms: Sequence[SymptomLog],
    now: datetime,
    *,
    companion_name: str,
    history_turns: int,
) -> list[ChatMessage]:
    """Same context, ending with a system instruction to open the conversation."""
    messages = build_context(
        elder, history, symptoms, now, companion_name=companion_name, history_turns=history_turns
    )
    candidates = follow_up_candidates(symptoms)
    hint = (
        f"If it feels natural, ask how her {candidates[0].label} is doing."
        if candidates
        else "Ask how she is feeling today."
    )
    lang = greeting_language(elder, history)
    instruction = render_prompt(
        "greet",
        nickname=elder.nickname,
        part_of_day=part_of_day(now),
        language=LANGUAGE_NAMES.get(lang, "English"),
        follow_up_hint=hint,
    )
    return [*messages, {"role": "system", "content": instruction}]
