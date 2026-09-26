"""ORM models: Elder / Message / SymptomLog / Alert (spec 3.8)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
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
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_message_role"),
        Index("ix_message_elder_created", "elder_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    elder_id: Mapped[int] = mapped_column(ForeignKey("elder.id"))
    role: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    audio_path: Mapped[str | None] = mapped_column(String(255))
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
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    is_read: Mapped[bool] = mapped_column(default=False)
