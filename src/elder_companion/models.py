"""ORM models (spec 3.12): Elder / Message / SymptomLog / Alert + Stage H tables.

Stage H, with family reminders (H5). The daily digest is not built in this version.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, false
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


REMINDER_STATUSES = ("mentioned", "confirmed", "declined", "no_response")


class Reminder(Base):
    """Something a family member wants the companion to bring up (spec 2.6, H5).

    Either daily at `schedule_time` or once on `schedule_date`. `text` is the family's own
    wording and is the only wording the companion uses: it never adds dose or medical advice.
    """

    __tablename__ = "reminder"
    __table_args__ = (
        CheckConstraint(
            "(schedule_time IS NOT NULL) OR (schedule_date IS NOT NULL)",
            name="ck_reminder_schedule",
        ),
        Index("ix_reminder_elder_active", "elder_id", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    from_member_id: Mapped[int | None] = mapped_column(ForeignKey("family_member.id"))
    text: Mapped[str] = mapped_column(Text)
    schedule_time: Mapped[str | None] = mapped_column(String(5))  # daily, "HH:MM" elder-local
    schedule_date: Mapped[date | None] = mapped_column()  # one-off, the elder's local date
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ReminderLog(Base):
    """One reminder on one of the elder's local days: was it raised, and what did she say."""

    __tablename__ = "reminder_log"
    __table_args__ = (
        CheckConstraint(
            "status IN ('mentioned', 'confirmed', 'declined', 'no_response')",
            name="ck_reminder_log_status",
        ),
        Index("ix_reminder_log_reminder_date", "reminder_id", "date", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reminder_id: Mapped[int] = mapped_column(ForeignKey("reminder.id"))
    date: Mapped[date] = mapped_column()  # the elder's local date it was raised
    status: Mapped[str] = mapped_column(String(20), default="mentioned")
    message_id: Mapped[int | None] = mapped_column(ForeignKey("message.id"))  # her answer
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class DailyDigest(Base):
    """One elder-local day, summarized once and kept (spec 3.10, H7).

    Today's dashboard summary and the weekly report both read from here, so a day is
    summarized once and the weekly page costs no extra model calls for days already seen.
    `has_private` records that part of the day was held back, without storing any of it.
    """

    __tablename__ = "daily_digest"
    __table_args__ = (
        CheckConstraint(
            "mood_score IS NULL OR (mood_score BETWEEN 1 AND 5)", name="ck_digest_mood"
        ),
        Index("ix_digest_elder_date", "elder_id", "date", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    date: Mapped[date] = mapped_column()  # the elder's local day
    summary: Mapped[str] = mapped_column(Text, default="")
    mood_score: Mapped[int | None] = mapped_column()  # 1 (low) to 5 (bright); None = no chat
    topics_json: Mapped[str] = mapped_column(Text, default="[]")
    has_private: Mapped[bool] = mapped_column(default=False, server_default=false())
    fingerprint: Mapped[str] = mapped_column(String(64), default="")  # regenerate when it moves
    generated_at: Mapped[datetime] = mapped_column(default=utcnow)
