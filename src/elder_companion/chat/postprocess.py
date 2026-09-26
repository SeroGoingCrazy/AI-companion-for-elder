"""Deterministic clean-up of model replies before they are saved and spoken."""

from __future__ import annotations

import re

_QUESTION_MARK = re.compile(r"[?？]")


def limit_questions(text: str, max_questions: int = 1) -> str:
    """Cut a reply after its first question if it asks more than `max_questions`.

    The persona prompt asks for one question per turn, but the model often splits an
    "A or B?" question in two (especially in Chinese). A spoken reply that asks two
    things is hard for an older listener to follow, so the rule is enforced in code.
    """
    marks = list(_QUESTION_MARK.finditer(text))
    if len(marks) <= max_questions:
        return text
    return text[: marks[max_questions - 1].end()].strip()


def tidy_reply(text: str) -> str:
    return limit_questions(text.strip())
