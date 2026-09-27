"""Build the message list sent to the chat model. Pure functions: callers do the DB queries."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from elder_companion.agenda.select import AgendaItem
from elder_companion.llm import ChatMessage
from elder_companion.models import Elder, MemoryItem, Message, ShareConsent, SymptomLog
from elder_companion.prompts import render_prompt

_CJK = re.compile(r"[一-鿿]")
LANGUAGE_NAMES = {"en": "English", "zh": "Chinese"}
NO_FOLLOW_UPS = "(nothing in particular)"
NO_MEMORY = "(nothing yet)"
DEFAULT_FAMILY_NAME = "her family"
MEMORY_KIND_LABELS = {"follow_up": "plan", "person": "person", "topic": "topic", "story": "story"}
PRIVATE_TAG = "[private: she asked you to keep this between you; never suggest telling her family]"
NO_SHARING = "(nothing decided yet)"
SHARING_LINES = {
    "share": "- {label}: she is happy for {family} to know; don't ask again",
    "private": (
        "- {label}: she wants this kept between you; never ask again or offer to tell anyone"
    ),
    "pending": "- {label}: you asked whether to tell {family} and she hasn't answered yet",
}


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


def format_memory(item: MemoryItem) -> str:
    """One line of companion memory for the system prompt (spec 3.3)."""
    line = f"- {item.subject} ({MEMORY_KIND_LABELS.get(item.kind, item.kind)}): {item.text}"
    if item.kind == "follow_up" and item.due_date is not None:
        line += f", worth asking about from {item.due_date:%A, %B} {item.due_date.day}"
        if item.status == "asked":
            line += " (you already asked once)"
    elif item.mention_count > 1:
        line += f", mentioned {item.mention_count} times"
    if item.private:
        line += f" {PRIVATE_TAG}"
    return line


def format_sharing(choices: Sequence[ShareConsent], family_name: str) -> str:
    return (
        "\n".join(
            SHARING_LINES[c.decision].format(label=c.label, family=family_name) for c in choices
        )
        or NO_SHARING
    )


def follow_up_candidates(symptoms: Sequence[SymptomLog]) -> list[SymptomLog]:
    return [s for s in symptoms if s.status != "resolved"]


def system_prompt(
    elder: Elder,
    symptoms: Sequence[SymptomLog],
    now: datetime,
    *,
    companion_name: str,
    memory: Sequence[MemoryItem] = (),
    family_name: str = DEFAULT_FAMILY_NAME,
    sharing: Sequence[ShareConsent] = (),
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
        memory="\n".join(format_memory(m) for m in memory) or NO_MEMORY,
        family_name=family_name,
        sharing=format_sharing(sharing, family_name),
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
    memory: Sequence[MemoryItem] = (),
    family_name: str = DEFAULT_FAMILY_NAME,
    sharing: Sequence[ShareConsent] = (),
) -> list[ChatMessage]:
    """System prompt + recent history. `history` must already include the current user message."""
    system = system_prompt(
        elder,
        symptoms,
        now,
        companion_name=companion_name,
        memory=memory,
        family_name=family_name,
        sharing=sharing,
    )
    return [{"role": "system", "content": system}, *history_messages(history, history_turns)]


def greeting_language(elder: Elder, history: Sequence[Message]) -> str:
    last_user = next((m for m in reversed(history) if m.role == "user"), None)
    return detect_language(last_user.text) if last_user else (elder.language or "en")


def format_agenda(agenda: Sequence[AgendaItem]) -> str:
    if not agenda:
        return ""
    lines = [
        f"{i}. {a.subject}: {a.text}" + (f" {PRIVATE_TAG}" if a.private else "")
        for i, a in enumerate(agenda, 1)
    ]
    return (
        "\n\nFrom earlier chats, worth asking about today:\n"
        + "\n".join(lines)
        + "\nMake item 1 your one question, asked with friendly curiosity, not like a form. "
        "The others can come up later in the chat if it feels natural. Never read them out "
        "as a list."
    )


def build_greet_context(
    elder: Elder,
    history: Sequence[Message],
    symptoms: Sequence[SymptomLog],
    now: datetime,
    *,
    companion_name: str,
    history_turns: int,
    memory: Sequence[MemoryItem] = (),
    agenda: Sequence[AgendaItem] = (),
    disclose_privacy: bool = False,
    family_name: str = DEFAULT_FAMILY_NAME,
    sharing: Sequence[ShareConsent] = (),
) -> list[ChatMessage]:
    """Same context, ending with a system instruction to open the conversation. The session
    agenda (spec 3.8) takes the greeting's one question; otherwise it follows up on a
    symptom. The first-ever greeting also explains the privacy rule (spec 2.7)."""
    messages = build_context(
        elder,
        history,
        symptoms,
        now,
        companion_name=companion_name,
        history_turns=history_turns,
        memory=memory,
        family_name=family_name,
        sharing=sharing,
    )
    candidates = follow_up_candidates(symptoms)
    if agenda:
        hint = ""
    elif candidates:
        hint = f"If it feels natural, ask how her {candidates[0].label} is doing."
    else:
        hint = "Ask how she is feeling today."
    disclosure = (
        "\n\nThis is your first chat together. Right after the greeting, tell her in one "
        "short, warm sentence that you will always ask her before telling "
        f"{family_name} anything personal, and that the only exception is her safety, like a "
        "fall or chest pain, which you would always tell them about."
        if disclose_privacy
        else ""
    )
    lang = greeting_language(elder, history)
    instruction = render_prompt(
        "greet",
        nickname=elder.nickname,
        part_of_day=part_of_day(now),
        language=LANGUAGE_NAMES.get(lang, "English"),
        follow_up_hint=hint,
        agenda=format_agenda(agenda),
        disclosure=disclosure,
    )
    return [*messages, {"role": "system", "content": instruction}]
