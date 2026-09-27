"""Ask before sharing: nothing personal reaches the family until she says yes (ADR 23).

When she mentions a (non-emergency) symptom or a personal matter, the companion asks whether
it may tell her family (companion.txt). Her choice is remembered per subject in
`share_consent`, so each subject is asked about once:

- pending / private: the message and the companion's reply are held back (`message.private`,
  `message.consent_keys`), and so is everything derived from them (symptom mentions, memory,
  non-urgent alerts), through the usual `privacy.py` read path.
- share: held messages whose subjects are now all shared are released.

Red-flag symptoms never wait (ADR 19): they are not consent subjects, and their alerts are
published right away. "Keep this between us" (memory extractor) still marks messages private
for good; `mark_private` clears their consent keys so a later "yes" cannot release them.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from elder_companion.db import utcnow
from elder_companion.llm import BaseLLMClient, ChatMessage, LLMError
from elder_companion.memory.extractor import Turn, format_turns, normalize_subject, verify_quote
from elder_companion.models import MemoryItem, Message, ShareConsent, SymptomLog, SymptomMention
from elder_companion.prompts import render_prompt
from elder_companion.symptoms.schema import SymptomCatalog, get_catalog

logger = logging.getLogger(__name__)

SCHEMA_NAME = "share_consent"
KEY_SEP = "|"
CONTEXT_TURNS = 3


# ---------- extraction contract ----------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonalTopic(_Strict):
    subject: str
    raw_quote: str


class ShareAnswer(_Strict):
    decision: Literal["share", "keep_private", "none"]
    subjects: list[str]


class ConsentExtraction(_Strict):
    personal_topics: list[PersonalTopic]
    answer: ShareAnswer


NO_CONSENT = ConsentExtraction(personal_topics=[], answer=ShareAnswer(decision="none", subjects=[]))


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {i}" for i in items) or "(none)"


class ConsentExtractor:
    def __init__(self, llm: BaseLLMClient) -> None:
        self._llm = llm
        self._schema: dict[str, Any] = ConsentExtraction.model_json_schema()

    def messages(
        self,
        message_id: int,
        user_text: str,
        recent_turns: Sequence[Turn],
        pending: Sequence[str],
        known: Sequence[str],
    ) -> list[ChatMessage]:
        # The utterance comes last, after a blank line (MockLLMClient matches only that part).
        user = (
            f"Waiting for her answer (you asked whether to tell her family):\n{_bullets(pending)}"
            f"\n\nPersonal topics seen before (reuse these subjects):\n{_bullets(known)}"
            f"\n\nRecent conversation (context only):\n{format_turns(recent_turns)}"
            f"\n\nCurrent utterance [#{message_id}]:\n{user_text}"
        )
        return [
            {"role": "system", "content": render_prompt("extract_consent")},
            {"role": "user", "content": user},
        ]

    def extract(
        self,
        message_id: int,
        user_text: str,
        recent_turns: Sequence[Turn] = (),
        pending: Sequence[str] = (),
        known: Sequence[str] = (),
    ) -> ConsentExtraction:
        """Raises LLMError. Topics whose quote is not in the utterance are dropped."""
        data = self._llm.extract_json(
            self.messages(message_id, user_text, recent_turns, pending, known),
            schema=self._schema,
            name=SCHEMA_NAME,
        )
        try:
            result = ConsentExtraction.model_validate(data)
        except ValidationError as e:
            raise LLMError(f"consent extraction did not match the schema: {e}") from e
        topics = {}
        for t in result.personal_topics:
            if t.subject.strip() and verify_quote(t.raw_quote, user_text):
                topics.setdefault(normalize_subject(t.subject), t)
        return result.model_copy(update={"personal_topics": list(topics.values())})


# ---------- keys ----------


def symptom_subject(canonical: str, label: str, catalog: SymptomCatalog) -> tuple[str, str]:
    """(key, label) of a symptom subject: one per catalog entry, one per free label for
    'other'."""
    if canonical == "other" or canonical not in catalog:
        return f"symptom:other:{normalize_subject(label)}", label
    return f"symptom:{canonical}", catalog.get(canonical).en.lower()


def topic_subject(subject: str) -> tuple[str, str]:
    return f"topic:{normalize_subject(subject)}", subject.strip()


def split_keys(value: str | None) -> list[str]:
    return [k for k in (value or "").split(KEY_SEP) if k]


# ---------- service ----------


@dataclass
class ConsentResult:
    held: list[int] = field(default_factory=list)  # message ids held back by this message
    released: list[int] = field(default_factory=list)  # message ids released by her "yes"
    decided: dict[str, str] = field(default_factory=dict)  # key -> share / private


class ConsentService:
    def __init__(
        self,
        session: Session,
        extractor: ConsentExtractor,
        catalog: SymptomCatalog | None = None,
    ) -> None:
        self._session = session
        self._extractor = extractor
        self._catalog = catalog or get_catalog()

    def process_message(self, message_id: int) -> ConsentResult:
        """Run after the symptom pipeline (it reads this message's symptom mentions). Never
        raises LLMError: if extraction fails, subjects without a "yes" are still held."""
        result = ConsentResult()
        msg = self._session.get(Message, message_id)
        if msg is None or msg.role != "user":
            return result
        reply = self._reply_to(msg)
        consents = {
            c.key: c
            for c in self._session.scalars(
                select(ShareConsent).where(ShareConsent.elder_id == msg.elder_id)
            )
        }
        pending = [c for c in consents.values() if c.decision == "pending"]
        try:
            extraction = self._extractor.extract(
                msg.id,
                msg.text,
                self._recent_turns(msg),
                pending=[c.label for c in pending],
                known=[c.label for c in consents.values() if c.key.startswith("topic:")],
            )
        except LLMError:
            logger.warning("consent extraction failed for message %s", msg.id, exc_info=True)
            extraction = NO_CONSENT

        # 1. Her answer to an earlier question comes first: "yes, and my knee still hurts"
        #    must not hold back the knee it just agreed to share.
        answer = extraction.answer
        if answer.decision != "none":
            decision = "share" if answer.decision == "share" else "private"
            for c in self._answered(answer.subjects, pending, msg):
                c.decision = decision
                c.updated_at = utcnow()
                result.decided[c.key] = decision
            self._session.flush()
            if decision == "share":
                result.released = self._release(msg.elder_id)
            elif result.decided:
                # "No, don't tell her" says there is something: hold the answer back too.
                result.held += self._hold([msg, reply], None)

        # 2. Subjects in this message.
        subjects = [
            symptom_subject(canonical, label, self._catalog)
            for canonical, label in self._symptoms(msg)
            if not self._catalog.is_red_flag(canonical)
        ]
        subjects += [topic_subject(t.subject) for t in extraction.personal_topics]
        keys = []
        for key, label in dict(subjects).items():
            if key not in consents:
                consents[key] = ShareConsent(
                    elder_id=msg.elder_id,
                    key=key,
                    label=label,
                    decision="pending",
                    asked_message_id=reply.id if reply else None,
                )
                self._session.add(consents[key])
            keys.append(key)
        if any(consents[k].decision != "share" for k in keys):
            result.held += self._hold([msg, reply], keys)
        self._session.commit()
        return result

    # ----- helpers -----

    def _answered(
        self, subjects: Sequence[str], pending: Sequence[ShareConsent], msg: Message
    ) -> list[ShareConsent]:
        """Pending subjects her answer is about: the ones she named, else those the
        companion asked about right before, else the latest one asked."""
        wanted = {normalize_subject(s) for s in subjects}
        named = [c for c in pending if normalize_subject(c.label) in wanted]
        if named:
            return named
        prev = self._session.scalars(
            select(Message.id)
            .where(Message.elder_id == msg.elder_id, Message.id < msg.id)
            .order_by(Message.id.desc())
            .limit(1)
        ).first()
        just_asked = [c for c in pending if c.asked_message_id == prev and prev is not None]
        if just_asked:
            return just_asked
        latest = max(pending, key=lambda c: (c.asked_message_id or 0, c.id or 0), default=None)
        return [latest] if latest else []

    def _hold(self, msgs: Sequence[Message | None], keys: Sequence[str] | None) -> list[int]:
        held = []
        for m in msgs:
            if m is None:
                continue
            m.private = True
            if keys:
                m.consent_keys = KEY_SEP.join(sorted(set(split_keys(m.consent_keys)) | set(keys)))
            held.append(m.id)
        self._session.flush()
        return held

    def _release(self, elder_id: int) -> list[int]:
        """Show held messages whose subjects she has now all agreed to share (and the memory
        items first seen in them)."""
        shared = set(
            self._session.scalars(
                select(ShareConsent.key).where(
                    ShareConsent.elder_id == elder_id, ShareConsent.decision == "share"
                )
            )
        )
        held = self._session.scalars(
            select(Message).where(
                Message.elder_id == elder_id,
                Message.private.is_(True),
                Message.consent_keys.is_not(None),
            )
        )
        released = [m for m in held if set(split_keys(m.consent_keys)) <= shared]
        for m in released:
            m.private = False
            m.consent_keys = None
        ids = [m.id for m in released]
        if ids:
            self._session.execute(
                update(MemoryItem).where(MemoryItem.message_id.in_(ids)).values(private=False)
            )
        self._session.flush()
        return ids

    def _reply_to(self, msg: Message) -> Message | None:
        nxt = self._session.scalars(
            select(Message)
            .where(Message.elder_id == msg.elder_id, Message.id > msg.id)
            .order_by(Message.id)
            .limit(1)
        ).first()
        return nxt if nxt is not None and nxt.role == "assistant" else None

    def _symptoms(self, msg: Message) -> list[tuple[str, str]]:
        rows = self._session.execute(
            select(SymptomLog.canonical, SymptomLog.label)
            .join(SymptomMention, SymptomMention.symptom_log_id == SymptomLog.id)
            .where(SymptomMention.message_id == msg.id)
        )
        return [(c, lbl) for c, lbl in rows]

    def _recent_turns(self, msg: Message) -> list[Turn]:
        stmt = (
            select(Message)
            .where(Message.elder_id == msg.elder_id, Message.id < msg.id)
            .order_by(Message.id.desc())
            .limit(2 * CONTEXT_TURNS)
        )
        return [Turn(m.id, m.role, m.text) for m in reversed(self._session.scalars(stmt).all())]


def sharing_choices(session: Session, elder_id: int) -> list[ShareConsent]:
    """Her sharing choices for the companion's context, oldest first."""
    return list(
        session.scalars(
            select(ShareConsent)
            .where(ShareConsent.elder_id == elder_id)
            .order_by(ShareConsent.updated_at, ShareConsent.id)
        )
    )
