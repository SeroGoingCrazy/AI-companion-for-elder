"""ORM models (spec 3.12): Elder / Message / SymptomLog / Alert + Stage H tables.

Stage H without reminders and the daily digest (not built in this version).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from elder_companion.db import Base, utcnow

# MVP has a single hard-coded elder (spec 1, non-goals).
DEFAULT_ELDER_ID = 1


class Elder(Base):
    __tablename__ = "elder"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    nickname: Mapped[str] = mapped_column(String(50))
    language: Mapped[str] = mapped_column(String(10), default="en")
    profile_text: Mapped[str] = mapped_column(Text, default="")
    # Set when a greeting first explains the privacy rule and its safety exception (spec 2.7).
    privacy_disclosed_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class FamilyMember(Base):
    __tablename__ = "family_member"

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    name: Mapped[str] = mapped_column(String(100))
    relation: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ChatSession(Base):
    """One visit to the elder app, started by the greeting."""

    __tablename__ = "chat_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    started_at: Mapped[datetime] = mapped_column(default=utcnow)


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_message_role"),
        Index("ix_message_elder_created", "elder_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("chat_session.id"))
    role: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    audio_path: Mapped[str | None] = mapped_column(String(255))
    # "Keep this between us": hidden from every family view, still in the agent's context.
    private: Mapped[bool] = mapped_column(default=False, server_default=false())
    # The text with personal details scrubbed, for family views (redaction.py). NULL until
    # the background pass has run; readers then fall back to rule-based redaction.
    family_text: Mapped[str | None] = mapped_column(Text)
    # Held back until she agrees to share: the share_consent keys ("|"-joined) this message
    # waits on. Set together with private=True; cleared when she asks to keep it private.
    consent_keys: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SymptomLog(Base):
    __tablename__ = "symptom_log"
    __table_args__ = (Index("ix_symptom_elder_canonical", "elder_id", "canonical", "last_seen"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    canonical: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(200))
    body_part: Mapped[str | None] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(20), default="unknown")
    duration: Mapped[str | None] = mapped_column(String(100))
    onset: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="new")
    raw_quote: Mapped[str] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message.id"))
    count: Mapped[int] = mapped_column(default=1)
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)


class Alert(Base):
    __tablename__ = "alert"
    __table_args__ = (
        CheckConstraint("type IN ('symptom', 'fall')", name="ck_alert_type"),
        CheckConstraint("level IN ('high', 'medium')", name="ck_alert_level"),
        Index("ix_alert_elder_created", "elder_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    type: Mapped[str] = mapped_column(String(20))
    level: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    snapshot_path: Mapped[str | None] = mapped_column(String(255))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    # The elder message behind a symptom alert (not part of the POST /api/alerts contract);
    # lets the family view hide non-bypass alerts raised inside a private segment.
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    is_read: Mapped[bool] = mapped_column(default=False)


class SymptomMention(Base):
    """One occurrence of a symptom, linked to the message it came from (for privacy)."""

    __tablename__ = "symptom_mention"
    __table_args__ = (Index("ix_symptom_mention_log", "symptom_log_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symptom_log_id: Mapped[int] = mapped_column(ForeignKey("symptom_log.id"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message.id"))
    raw_quote: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), default="unknown")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


MEMORY_KINDS = ("follow_up", "person", "topic", "story")


class MemoryItem(Base):
    """Small life details the companion remembers (spec 3.7)."""

    __tablename__ = "memory_item"
    __table_args__ = (
        CheckConstraint("kind IN ('follow_up', 'person', 'topic', 'story')", name="ck_memory_kind"),
        CheckConstraint("status IN ('open', 'asked', 'expired')", name="ck_memory_status"),
        Index("ix_memory_elder_kind", "elder_id", "kind", "last_seen"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    kind: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    raw_quote: Mapped[str] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message.id"))
    # Private only while every mention was private.
    private: Mapped[bool] = mapped_column(default=False, server_default=false())
    mention_count: Mapped[int] = mapped_column(default=1)
    due_date: Mapped[date | None] = mapped_column()  # follow_up: the elder's local date
    status: Mapped[str] = mapped_column(String(10), default="open")
    retelling: Mapped[str | None] = mapped_column(Text)  # story: cached memoir retelling
    first_seen: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(default=utcnow)


CLAIM_TARGETS = ("alert", "memory_item", "symptom_log")


class Claim(Base):
    """A family member taking something on: "Ben: I'll call her doctor"."""

    __tablename__ = "claim"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('alert', 'memory_item', 'symptom_log')", name="ck_claim_target"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[int] = mapped_column()
    member_id: Mapped[int] = mapped_column(ForeignKey("family_member.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column()


CONSENT_DECISIONS = ("pending", "share", "private")


class ShareConsent(Base):
    """Her choice about sharing one subject with the family (consent.py): a symptom type
    ("symptom:joint_pain") or a personal topic ("topic:money worries"). Asked once, then
    remembered until she changes her mind."""

    __tablename__ = "share_consent"
    __table_args__ = (
        UniqueConstraint("elder_id", "key", name="uq_share_consent_key"),
        CheckConstraint(
            "decision IN ('pending', 'share', 'private')", name="ck_share_consent_decision"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    key: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(120))  # "joint pain", "money worries"
    decision: Mapped[str] = mapped_column(String(10), default="pending")
    asked_message_id: Mapped[int | None] = mapped_column(ForeignKey("message.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
