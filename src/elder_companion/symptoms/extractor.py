"""Turn one elder utterance into structured symptoms (spec 3.4.2)."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from pydantic import ValidationError

from elder_companion.llm import BaseLLMClient, ChatMessage, LLMError
from elder_companion.prompts import render_prompt
from elder_companion.symptoms.schema import (
    SCHEMA_NAME,
    SymptomCatalog,
    SymptomItem,
    extraction_schema,
    get_catalog,
    parse_extraction,
)

logger = logging.getLogger(__name__)

_NON_WORD = re.compile(r"[\W_]+")
SPEAKERS = {"user": "Elder", "assistant": "Companion"}
CURRENT_HEADING = "Her current utterance:"


def _normalize(text: str) -> str:
    # Ignore case, spaces and punctuation: models often drop a comma or change "’" to "'".
    return _NON_WORD.sub("", text.casefold())


def verify_quote(item: SymptomItem, text: str) -> bool:
    """Anti-hallucination check: raw_quote must appear in what the elder actually said."""
    quote = _normalize(item.raw_quote)
    return bool(quote) and quote in _normalize(text)


def dedupe_key(item: SymptomItem) -> tuple[str, str]:
    """Symptoms are identified by canonical; `other` is told apart by its label."""
    label = item.label.strip().casefold() if item.canonical == "other" else ""
    return item.canonical, label


def format_turns(turns: Sequence[ChatMessage]) -> str:
    lines = [f"{SPEAKERS.get(t['role'], t['role'])}: {t['content']}" for t in turns]
    return "\n".join(lines) or "(none)"


class SymptomExtractor:
    def __init__(self, llm: BaseLLMClient, catalog: SymptomCatalog | None = None) -> None:
        self._llm = llm
        self._catalog = catalog or get_catalog()
        self._schema = extraction_schema(self._catalog)

    def messages(self, user_text: str, recent_turns: Sequence[ChatMessage]) -> list[ChatMessage]:
        symptom_list = "\n".join(f"- {d.canonical}: {d.hint}" for d in self._catalog)
        system = render_prompt("extract_symptoms", symptom_list=symptom_list)
        # The context sits right next to the utterance: at the end of the system prompt the
        # model often missed follow-ups like "much better today" (about the knee).
        # The utterance comes last, after a blank line (MockLLMClient matches only that part).
        user = (
            f"Recent conversation (context only):\n{format_turns(recent_turns)}\n\n"
            f"{CURRENT_HEADING}\n{user_text}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def extract(
        self, user_text: str, recent_turns: Sequence[ChatMessage] = ()
    ) -> list[SymptomItem]:
        """Symptoms the elder mentioned in `user_text`. Raises LLMError if the call fails."""
        data = self._llm.extract_json(
            self.messages(user_text, recent_turns), schema=self._schema, name=SCHEMA_NAME
        )
        try:
            items = parse_extraction(data, self._catalog).symptoms
        except ValidationError as e:
            raise LLMError(f"extraction did not match the schema: {e}") from e

        kept: dict[tuple[str, str], SymptomItem] = {}
        for item in items:
            if not verify_quote(item, user_text):
                logger.info("dropping %s: %r not in utterance", item.canonical, item.raw_quote)
                continue
            kept.setdefault(dedupe_key(item), item)
        return list(kept.values())
