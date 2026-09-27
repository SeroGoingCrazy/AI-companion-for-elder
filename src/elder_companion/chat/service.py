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

from elder_companion.agenda.service import AgendaService
from elder_companion.chat.context import (
    DEFAULT_FAMILY_NAME,
    build_context,
    build_greet_context,
    detect_language,
    greeting_language,
    part_of_day,
)
from elder_companion.chat.postprocess import guard_medical_advice, tidy_reply
from elder_companion.consent import sharing_choices
from elder_companion.db import utcnow
from elder_companion.elders import get_elder
from elder_companion.llm import BaseLLMClient, LLMError
from elder_companion.models import ChatSession, Elder, FamilyMember, MemoryItem, Message, SymptomLog
from elder_companion.settings import AgendaSettings, ChatSettings, MemorySettings

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
MAX_MEMORY_ITEMS = 12  # most recently mentioned companion-memory items in the context
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
        agenda: AgendaSettings | None = None,
        memory: MemorySettings | None = None,
    ) -> None:
        self._session = session
        self._llm = llm
        self._s = settings
        self._audio_dir = audio_dir
        self._now = now or (lambda: datetime.now(settings.tz))
        self._agenda = AgendaService(
            session, agenda or AgendaSettings(), memory or MemorySettings()
        )

    def reply(self, elder_id: int | None, text: str) -> ChatResult:
        text = text.strip()
        if not text:
            raise ValueError("text must not be empty")
        elder = get_elder(self._session, elder_id)
        session_id = self._current_session(elder)
        user_msg = self._save(elder, "user", text, session_id)
        now = self._now()
        messages = build_context(
            elder,
            self._history(elder),
            self._recent_symptoms(elder, now),
            now,
            companion_name=self._s.companion_name,
            history_turns=self._s.history_turns,
            memory=self._memory(elder),
            family_name=self._family_name(elder),
            sharing=sharing_choices(self._session, elder.id),
        )
        try:
            reply = tidy_reply(self._llm.chat(messages, max_tokens=self._s.max_reply_tokens))
            reply = guard_medical_advice(reply)
            fallback = False
        except LLMError:
            logger.warning("chat LLM call failed; using fallback reply", exc_info=True)
            reply, fallback = FALLBACK_REPLY[detect_language(text)], True
        assistant_msg = self._save(elder, "assistant", reply, session_id)
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
        """Start a session: open with the agenda (due follow-ups, spec 3.8) and, the very
        first time, the privacy disclosure (spec 2.7). Items are marked delivered only when
        the model's greeting (not the canned fallback) carried them."""
        elder = get_elder(self._session, elder_id)
        now = self._now()
        chat = ChatSession(elder_id=elder.id, started_at=utcnow())
        self._session.add(chat)
        self._session.flush()
        history = self._history(elder)
        agenda = self._agenda.select(elder.id, now)
        disclose = elder.privacy_disclosed_at is None
        messages = build_greet_context(
            elder,
            history,
            self._recent_symptoms(elder, now),
            now,
            companion_name=self._s.companion_name,
            history_turns=self._s.history_turns,
            memory=self._memory(elder),
            agenda=agenda,
            disclose_privacy=disclose,
            family_name=self._family_name(elder),
            sharing=sharing_choices(self._session, elder.id),
        )
        try:
            reply = tidy_reply(self._llm.chat(messages, max_tokens=self._s.max_reply_tokens))
            reply = guard_medical_advice(reply)
            fallback = False
        except LLMError:
            logger.warning("greet LLM call failed; using fallback greeting", exc_info=True)
            lang = greeting_language(elder, history)
            template = FALLBACK_GREETING.get(lang, FALLBACK_GREETING["en"])
            reply = template.format(part_of_day=part_of_day(now), nickname=elder.nickname)
            fallback = True
        assistant_msg = self._save(elder, "assistant", reply, chat.id)
        if not fallback:
            self._agenda.mark_carried(agenda, now.date())
            if disclose:
                elder.privacy_disclosed_at = utcnow()
        self._session.commit()
        return ChatResult(assistant_msg.id, "", reply, fallback)

    def synthesize(self, message_id: int, voice: str | None = None) -> Path | None:
        """Return a cached mp3 for an assistant message, generating it once per voice (None =
        the configured default). None if TTS fails."""
        if self._audio_dir is None:
            raise RuntimeError("ChatService was created without an audio_dir")
        msg = self._session.get(Message, message_id)
        if msg is None or msg.role != "assistant":
            raise MessageNotFound(message_id)
        path = self._audio_dir / (f"{msg.id}.{voice}.mp3" if voice else f"{msg.id}.mp3")
        if path.exists():
            return path
        try:
            data = self._llm.tts(msg.text, voice=voice)
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

    def _save(self, elder: Elder, role: str, text: str, session_id: int | None) -> Message:
        msg = Message(elder_id=elder.id, session_id=session_id, role=role, text=text)
        self._session.add(msg)
        self._session.commit()
        return msg

    def _current_session(self, elder: Elder) -> int:
        """The latest session (the greeting starts one); a chat without a greeting opens one."""
        latest = self._session.scalar(
            select(ChatSession.id)
            .where(ChatSession.elder_id == elder.id)
            .order_by(ChatSession.id.desc())
            .limit(1)
        )
        if latest is not None:
            return latest
        chat = ChatSession(elder_id=elder.id, started_at=utcnow())
        self._session.add(chat)
        self._session.flush()
        return chat.id

    def _memory(self, elder: Elder) -> list[MemoryItem]:
        """Companion memory for the context, private items included: the AI still remembers
        them, tagged never-relay (spec 2.7)."""
        stmt = (
            select(MemoryItem)
            .where(MemoryItem.elder_id == elder.id, MemoryItem.status != "expired")
            .order_by(MemoryItem.last_seen.desc(), MemoryItem.id.desc())
            .limit(MAX_MEMORY_ITEMS)
        )
        return list(reversed(self._session.scalars(stmt).all()))

    def _family_name(self, elder: Elder) -> str:
        name = self._session.scalar(
            select(FamilyMember.name)
            .where(FamilyMember.elder_id == elder.id)
            .order_by(FamilyMember.id)
            .limit(1)
        )
        return name or DEFAULT_FAMILY_NAME

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
