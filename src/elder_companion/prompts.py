"""Prompt templates in config/prompts/*.txt, filled with string.Template ($name placeholders)."""

from __future__ import annotations

from functools import lru_cache
from string import Template

from elder_companion.settings import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "config" / "prompts"


@lru_cache
def load_prompt(name: str) -> Template:
    return Template((PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8").strip())


def render_prompt(prompt_name: str, /, **values: object) -> str:
    """Fill a prompt. A missing placeholder raises KeyError instead of leaking '$x' to the model.

    `prompt_name` is positional-only so any placeholder (including `$name`) can be a keyword.
    """
    return load_prompt(prompt_name).substitute(**values)
