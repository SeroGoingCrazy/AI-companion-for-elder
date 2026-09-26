"""Chat turn orchestration: persist messages, build context, call the LLM, degrade gracefully."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from elder_companion.chat.context import (
    build_context,
    build_greet_context,
    detect_language,
    greeting_language,
    part_of_day,
)
from elder_companion.chat.postprocess import tidy_reply
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
# Transcripts shorter than this are treated as noise (a cough, a mis-press).
MIN_TRANSCRIPT_CHARS = 2


class MessageNotFound(LookupError):
    pass


@dataclass(frozen=True)
class ChatResult:
    message_id: int | None  # the assistant message (used to fetch its TTS audio)
    user_text: str
    reply_text: str
    fallback: bool = False  # True when the LLM failed and a canned reply was used
    need_retry: bool = False  # True when speech could not be understood; nothing was saved
    user_message_id: int | None = None  # the elder's message (fed to the symptom pipeline)


NEED_RETRY = ChatResult(message_id=None, user_text="", reply_text="", need_retry=True)


class ChatService:
    def __init__(
        self,
        session: Session,
        llm: BaseLLMClient,
        settings: ChatSettings,
        audio_dir: Path | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._llm = llm
        self._s = settings
        self._audio_dir = audio_dir
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
            reply = tidy_reply(self._llm.chat(messages, max_tokens=self._s.max_reply_tokens))
            fallback = False
        except LLMError:
            logger.warning("chat LLM call failed; using fallback reply", exc_info=True)
            reply, fallback = FALLBACK_REPLY[detect_language(text)], True
        assistant_msg = self._save(elder, "assistant", reply)
        return ChatResult(
            assistant_msg.id, user_msg.text, reply, fallback, user_message_id=user_msg.id
        )

    def reply_audio(self, elder_id: int | None, audio: bytes, *, filename: str) -> ChatResult:
        """Transcribe a voice clip, then reply. Unintelligible audio returns NEED_RETRY."""
        elder = get_elder(self._session, elder_id)  # 404 before spending an API call
        try:
            text = self._llm.transcribe(audio, filename=filename)
        except LLMError:
            logger.warning("transcription failed; asking the elder to repeat", exc_info=True)
            return NEED_RETRY
        if len(text.strip()) < MIN_TRANSCRIPT_CHARS:
            return NEED_RETRY
        return self.reply(elder.id, text)

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
            reply = tidy_reply(self._llm.chat(messages, max_tokens=self._s.max_reply_tokens))
            fallback = False
        except LLMError:
            logger.warning("greet LLM call failed; using fallback greeting", exc_info=True)
            lang = greeting_language(elder, history)
            template = FALLBACK_GREETING.get(lang, FALLBACK_GREETING["en"])
            reply = template.format(part_of_day=part_of_day(now), nickname=elder.nickname)
            fallback = True
        assistant_msg = self._save(elder, "assistant", reply)
        return ChatResult(assistant_msg.id, "", reply, fallback)

    def synthesize(self, message_id: int) -> Path | None:
        """Return a cached mp3 for an assistant message, generating it once. None if TTS fails."""
        if self._audio_dir is None:
            raise RuntimeError("ChatService was created without an audio_dir")
        msg = self._session.get(Message, message_id)
        if msg is None or msg.role != "assistant":
            raise MessageNotFound(message_id)
        path = self._audio_dir / f"{msg.id}.mp3"
        if path.exists():
            return path
        try:
            data = self._llm.tts(msg.text)
        except LLMError:
            logger.warning("TTS failed for message %s; client falls back to text", msg.id)
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: concurrent requests never see a half-written file
        msg.audio_path = f"audio/{path.name}"
        self._session.commit()
        return path

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
