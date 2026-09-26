"""Chat turn orchestration: persist messages, build context, call the LLM, degrade gracefully."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.chat.context import (
    build_context,
    build_greet_context,
    detect_language,
    greeting_language,
    part_of_day,
)
from elder_companion.elders import get_elder
from elder_companion.llm import BaseLLMClient, LLMError
from elder_companion.models import Elder, Message, SymptomLog
from elder_companion.settings import ChatSettings

logger = logging.getLogger(__name__)

FALLBACK_REPLY = {
    "en": "Sorry, I lost my train of thought for a moment. I'm still right here with you. "
    "Could you say that again?",
    "zh": "不好意思，我刚才走神了一下。我还在这儿陪着你，能再跟我说一遍吗？",
}
FALLBACK_GREETING = {
    "en": "Good {part_of_day}, {nickname}! How are you feeling today?",
    "zh": "{nickname}，你好呀！今天感觉怎么样？",
}
MAX_FOLLOW_UPS = 5


@dataclass(frozen=True)
class ChatResult:
    message_id: int  # the assistant message (used later to fetch its TTS audio)
    user_text: str
    reply_text: str
    fallback: bool = False  # True when the LLM failed and a canned reply was used


class ChatService:
    def __init__(
        self,
        session: Session,
        llm: BaseLLMClient,
        settings: ChatSettings,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._llm = llm
        self._s = settings
        self._now = now or (lambda: datetime.now(settings.tz))

    def reply(self, elder_id: int | None, text: str) -> ChatResult:
        text = text.strip()
        if not text:
            raise ValueError("text must not be empty")
        elder = get_elder(self._session, elder_id)
        user_msg = self._save(elder, "user", text)
        now = self._now()
        messages = build_context(
            elder,
            self._history(elder),
            self._recent_symptoms(elder, now),
            now,
            companion_name=self._s.companion_name,
            history_turns=self._s.history_turns,
        )
        try:
            reply, fallback = self._llm.chat(messages, max_tokens=self._s.max_reply_tokens), False
        except LLMError:
            logger.warning("chat LLM call failed; using fallback reply", exc_info=True)
            reply, fallback = FALLBACK_REPLY[detect_language(text)], True
        assistant_msg = self._save(elder, "assistant", reply)
        return ChatResult(assistant_msg.id, user_msg.text, reply, fallback)

    def greet(self, elder_id: int | None) -> ChatResult:
        elder = get_elder(self._session, elder_id)
        now = self._now()
        history = self._history(elder)
        messages = build_greet_context(
            elder,
            history,
            self._recent_symptoms(elder, now),
            now,
            companion_name=self._s.companion_name,
            history_turns=self._s.history_turns,
        )
        try:
            reply, fallback = self._llm.chat(messages, max_tokens=self._s.max_reply_tokens), False
        except LLMError:
            logger.warning("greet LLM call failed; using fallback greeting", exc_info=True)
            lang = greeting_language(elder, history)
            template = FALLBACK_GREETING.get(lang, FALLBACK_GREETING["en"])
            reply = template.format(part_of_day=part_of_day(now), nickname=elder.nickname)
            fallback = True
        assistant_msg = self._save(elder, "assistant", reply)
        return ChatResult(assistant_msg.id, "", reply, fallback)

    def _save(self, elder: Elder, role: str, text: str) -> Message:
        msg = Message(elder_id=elder.id, role=role, text=text)
        self._session.add(msg)
        self._session.commit()
        return msg

    def _history(self, elder: Elder) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.elder_id == elder.id)
            .order_by(Message.id.desc())
            .limit(2 * self._s.history_turns)
        )
        return list(reversed(self._session.scalars(stmt).all()))

    def _recent_symptoms(self, elder: Elder, now: datetime) -> list[SymptomLog]:
        since = now.astimezone(UTC).replace(tzinfo=None) - timedelta(hours=self._s.follow_up_hours)
        stmt = (
            select(SymptomLog)
            .where(
                SymptomLog.elder_id == elder.id,
                SymptomLog.last_seen >= since,
                SymptomLog.status != "resolved",
            )
            .order_by(SymptomLog.last_seen.desc())
            .limit(MAX_FOLLOW_UPS)
        )
        return list(self._session.scalars(stmt))
