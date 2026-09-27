"""Scrub personal details from the conversation before the family sees it.

Two passes, on top of the parent-controlled privacy marks in `privacy`:

- `redact_rules`: deterministic regexes for structured details (phone, email, ID and card
  numbers, passwords / PINs, street addresses). Always applied, also to the LLM's output, and
  used alone for messages the LLM pass has not reached (or failed on).
- `Redactor`: one LLM call per chat turn that also catches free-form details (a neighbour's
  full name with her address, a bank name with an account, ...). Its output is stored in
  `message.family_text`.

The companion's own context always uses the original `message.text`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from elder_companion.llm import BaseLLMClient, ChatMessage, LLMError
from elder_companion.models import Message
from elder_companion.prompts import render_prompt

logger = logging.getLogger(__name__)

SCHEMA_NAME = "family_redaction"

# Order matters: specific patterns first, so a card number is not reported as a phone number.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    # "password is hunter2", "PIN: 4821", "密码是123456"
    (
        re.compile(r"(?i)\b(password|passcode|security code)(\s*(?:is|was|:|=)\s*)[^\s,.;!?]+"),
        r"\1\2[password]",
    ),
    (
        re.compile(r"(?i)\b(pin(?:\s*(?:code|number))?)(\s*(?:is|was|:|=)?\s*)\d{3,}"),
        r"\1\2[password]",
    ),
    (re.compile(r"(密码|口令|验证码)(\s*(?:是|为|:|：)?\s*)[A-Za-z0-9]+"), r"\1\2[密码]"),
    # Digit lookarounds, not \b: CJK characters count as word characters in Python.
    (re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"), "[ID number]"),  # US SSN
    (re.compile(r"(?<!\d)\d{17}[\dXx](?![\dA-Za-z])"), "[ID number]"),  # PRC resident ID
    (re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), "[card number]"),
    # US: (555) 123-4567, 555-123-4567, +1 555 123 4567; CN mobile: 138 1234 5678
    (
        re.compile(r"(?<![\d-])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?![\d-])"),
        "[phone number]",
    ),
    (re.compile(r"(?<!\d)1[3-9]\d[ -]?\d{4}[ -]?\d{4}(?!\d)"), "[phone number]"),
    # Account / policy / member numbers: any remaining run of 6+ digits.
    (re.compile(r"(?<![\d.:/])\d{6,}(?![\d.:/])"), "[number]"),
    (
        re.compile(
            r"\b\d{1,6}\s+(?:[A-Z][\w'.-]*\s+){1,3}"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|"
            r"Place|Pl|Terrace|Circle|Cir|Parkway|Pkwy)\b\.?"
            r"(?:,?\s*(?:Apt|Apartment|Unit|Suite|#)\s*\w+)?"
        ),
        "[address]",
    ),
    # The street name stops at words like 住在 / 是, so "我住在长安路88号" keeps "我住在".
    (
        re.compile(r"(?:(?![在住是到去的址])[一-鿿]){1,12}(?:路|街|大道|巷|弄)\d+号(?:[\d-]+室)?"),
        "[地址]",
    ),
)


def redact_rules(text: str) -> str:
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text


def family_text(msg: Message) -> str:
    """What the family may read of a message (privacy marks are handled by the caller)."""
    return redact_rules(msg.family_text if msg.family_text is not None else msg.text)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RedactedMessage(_Strict):
    id: int
    text: str


class RedactionOut(_Strict):
    messages: list[RedactedMessage]


class Redactor:
    def __init__(self, llm: BaseLLMClient) -> None:
        self._llm = llm
        self._schema: dict[str, Any] = RedactionOut.model_json_schema()

    def messages(self, msgs: Sequence[Message]) -> list[ChatMessage]:
        lines = "\n".join(
            f"[#{m.id}] {'Elder' if m.role == 'user' else 'Companion'}: {m.text}" for m in msgs
        )
        return [
            {"role": "system", "content": render_prompt("redact_family")},
            {"role": "user", "content": f"Messages:\n\n{lines}"},
        ]

    def redact(self, msgs: Sequence[Message]) -> dict[int, str]:
        """{message id: redacted text}. Raises LLMError. Ids the model invented or dropped are
        left out; rule redaction is applied to everything it returns."""
        data = self._llm.extract_json(self.messages(msgs), schema=self._schema, name=SCHEMA_NAME)
        try:
            out = RedactionOut.model_validate(data)
        except ValidationError as e:
            raise LLMError(f"redaction did not match the schema: {e}") from e
        wanted = {m.id for m in msgs}
        return {
            r.id: redact_rules(r.text) for r in out.messages if r.id in wanted and r.text.strip()
        }


def redact_messages(
    session_factory: sessionmaker[Session], redactor: Redactor, message_ids: Sequence[int]
) -> None:
    """Fill `family_text` for these messages. Never raises: on LLM failure the rule-based
    version is stored, so every message ends up processed."""
    ids = [i for i in message_ids if i is not None]
    if not ids:
        return
    with session_factory() as session:
        msgs = list(
            session.scalars(select(Message).where(Message.id.in_(ids)).order_by(Message.id))
        )
        if not msgs:
            return
        try:
            redacted = redactor.redact(msgs)
        except LLMError:
            logger.warning("family redaction failed for %s; using rules only", ids, exc_info=True)
            redacted = {}
        for m in msgs:
            m.family_text = redacted.get(m.id) or redact_rules(m.text)
        session.commit()
