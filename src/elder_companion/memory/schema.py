"""The memory extractor's structured-output contract (spec 3.7)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_NAME = "memory_extraction"

MemoryKind = Literal["follow_up", "person", "topic", "story"]


class _Strict(BaseModel):
    # extra=forbid emits additionalProperties: false, which OpenAI strict mode requires.
    model_config = ConfigDict(extra="forbid")


class MemoryItemIn(_Strict):
    kind: MemoryKind
    subject: str = Field(description="Short stable name, e.g. 'orchid', 'Rosa'")
    text: str = Field(description="One short English sentence from her point of view")
    due_in_days: int | None = Field(description="follow_up only: days until it makes sense to ask")
    raw_quote: str = Field(description="Exact words copied from her current utterance")


class PrivacyRequest(_Strict):
    requested: bool
    covers_message_ids: list[int]


class MemoryExtraction(_Strict):
    items: list[MemoryItemIn]
    privacy_request: PrivacyRequest


NO_PRIVACY = PrivacyRequest(requested=False, covers_message_ids=[])


def memory_schema() -> dict[str, Any]:
    return MemoryExtraction.model_json_schema()
