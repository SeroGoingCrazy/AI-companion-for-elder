"""The memory extractor's structured-output contract (spec 3.7)."""

from __future__ import annotations

import copy
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


AckStatus = Literal["confirmed", "declined"]


class ReminderAck(_Strict):
    """Her answer to a reminder the companion raised earlier in this session (spec 3.8)."""

    reminder_id: int = Field(description="The id of the reminder she is answering")
    status: AckStatus = Field(description="confirmed = she did it, declined = she has not")


class MemoryExtraction(_Strict):
    items: list[MemoryItemIn]
    privacy_request: PrivacyRequest
    # Defaulted so older canned fixtures stay valid; `memory_schema` still marks it required,
    # which is what OpenAI strict mode needs.
    reminder_acks: list[ReminderAck] = []


NO_PRIVACY = PrivacyRequest(requested=False, covers_message_ids=[])


def memory_schema() -> dict[str, Any]:
    """Strict mode requires every property to be listed in `required`, including the ones
    that carry a default on the model."""
    schema = copy.deepcopy(MemoryExtraction.model_json_schema())
    schema["required"] = list(schema["properties"])
    return schema
