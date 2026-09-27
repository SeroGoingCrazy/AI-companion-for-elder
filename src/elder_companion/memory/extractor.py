"""Pick out the small life details worth remembering from one utterance (spec 3.7).

A separate call and prompt from symptom extraction (ADR 17), so a memory-prompt change can
never regress red-flag recall.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from elder_companion.llm import BaseLLMClient, ChatMessage, LLMError
from elder_companion.memory.schema import SCHEMA_NAME, MemoryExtraction, memory_schema
from elder_companion.prompts import render_prompt
from elder_companion.symptoms.extractor import SPEAKERS, _normalize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Turn:
    message_id: int
    role: str
    text: str


def verify_quote(quote: str, text: str) -> bool:
    q = _normalize(quote)
    return bool(q) and q in _normalize(text)


def normalize_subject(subject: str) -> str:
    return " ".join(subject.casefold().split())


def format_turns(turns: Sequence[Turn]) -> str:
    lines = [f"[#{t.message_id}] {SPEAKERS.get(t.role, t.role)}: {t.text}" for t in turns]
    return "\n".join(lines) or "(none)"


class MemoryExtractor:
    def __init__(self, llm: BaseLLMClient) -> None:
        self._llm = llm
        self._schema = memory_schema()

    def messages(
        self, message_id: int, user_text: str, recent_turns: Sequence[Turn]
    ) -> list[ChatMessage]:
        # The utterance comes last, after a blank line (MockLLMClient matches only that part).
        user = (
            f"Recent conversation (context only):\n{format_turns(recent_turns)}\n\n"
            f"Current utterance [#{message_id}]:\n{user_text}"
        )
        return [
            {"role": "system", "content": render_prompt("extract_memory")},
            {"role": "user", "content": user},
        ]

    def extract(
        self, message_id: int, user_text: str, recent_turns: Sequence[Turn] = ()
    ) -> MemoryExtraction:
        """Raises LLMError if the call fails. Items whose quote is not in the utterance and
        duplicates (same kind + subject) are dropped."""
        data = self._llm.extract_json(
            self.messages(message_id, user_text, recent_turns),
            schema=self._schema,
            name=SCHEMA_NAME,
        )
        try:
            result = MemoryExtraction.model_validate(data)
        except ValidationError as e:
            raise LLMError(f"memory extraction did not match the schema: {e}") from e
        kept = {}
        for item in result.items:
            if not item.subject.strip() or not verify_quote(item.raw_quote, user_text):
                logger.info(
                    "dropping memory %s/%r: quote not in utterance", item.kind, item.subject
                )
                continue
            kept.setdefault((item.kind, normalize_subject(item.subject)), item)
        return result.model_copy(update={"items": list(kept.values())})
