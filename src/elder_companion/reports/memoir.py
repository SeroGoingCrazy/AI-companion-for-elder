"""Family memoir (spec 2.5, 3.10): the stories she tells, kept as a keepsake page.

Each story shows her verbatim words next to a lightly polished retelling. The retelling is
generated once and stored on the memory item, so later loads make no LLM calls; if the model
is unavailable the page simply shows her words. Reads through `privacy`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from elder_companion.chat.context import detect_language
from elder_companion.llm import BaseLLMClient, LLMError
from elder_companion.models import Elder, MemoryItem
from elder_companion.privacy import visible_memory
from elder_companion.prompts import render_prompt

logger = logging.getLogger(__name__)

MAX_RETELLING_TOKENS = 200


@dataclass(frozen=True)
class MemoirEntry:
    id: int
    subject: str
    told_at: datetime  # the elder's local time
    excerpt: str  # her exact words
    retelling: str | None
    lang: str  # of the excerpt, for the page's lang attribute


def retell(llm: BaseLLMClient, elder: Elder, item: MemoryItem) -> str:
    """Raises LLMError."""
    system = render_prompt("memoir", nickname=elder.nickname)
    user = f"About: {item.text}\n\nHer words:\n{item.raw_quote}"
    text = llm.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=MAX_RETELLING_TOKENS,
    )
    return text.strip()


def build_memoir(
    session: Session, llm: BaseLLMClient, elder: Elder, now: datetime
) -> list[MemoirEntry]:
    """Visible stories, oldest first; missing retellings are generated and cached."""
    stories = visible_memory(session, elder.id, ["story"])
    entries = []
    for item in stories:
        if item.retelling is None:
            try:
                item.retelling = retell(llm, elder, item)
                session.commit()
            except LLMError:
                logger.warning("memoir retelling failed for item %s", item.id, exc_info=True)
        told_at = item.first_seen.replace(tzinfo=UTC).astimezone(now.tzinfo)
        entries.append(
            MemoirEntry(
                item.id,
                item.subject,
                told_at,
                item.raw_quote,
                item.retelling,
                detect_language(item.raw_quote),
            )
        )
    return entries
