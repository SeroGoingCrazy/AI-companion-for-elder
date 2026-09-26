"""Alert API schemas. AlertIn is the frozen cross-process contract with fall-mcp (spec 5.5)."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from elder_companion.db import UtcDateTime

AlertType = Literal["symptom", "fall"]
AlertLevel = Literal["high", "medium"]


class AlertIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AlertType
    level: AlertLevel
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=2000)
    snapshot_path: str | None = Field(
        default=None, description="relative to the data dir, e.g. snapshots/20261001_101530.jpg"
    )
    ref_id: str | None = Field(default=None, max_length=64, description="e.g. fall event_id")
    elder_id: int | None = Field(default=None, description="defaults to the demo elder")

    @field_validator("snapshot_path")
    @classmethod
    def _must_be_relative(cls, v: str | None) -> str | None:
        if v is None:
            return v
        normalized = v.replace("\\", "/")
        p = PurePosixPath(normalized)
        if not normalized or p.is_absolute() or ".." in p.parts or ":" in normalized:
            raise ValueError("must be a relative path under the data dir, e.g. snapshots/x.jpg")
        return p.as_posix()


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    elder_id: int
    type: AlertType
    level: AlertLevel
    title: str
    content: str
    snapshot_path: str | None
    ref_id: str | None
    created_at: UtcDateTime
    is_read: bool
