"""UI strings in English and Simplified Chinese (config/i18n/*.yaml).

The elder using the app and the family reading the dashboard may not share a language,
so the choice is per browser, not per elder: a `lang` cookie set by the switch in the
header, overridable with `?lang=` for demos. Anything the model writes (replies, the
daily summary) follows the language the elder actually spoke, which the prompt already
handles; this module only covers text the app itself owns.

Symptom names are not here. They live in config/symptoms.yaml, which already carries
`en` and `zh` for every canonical, and that file stays the single source of truth.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from elder_companion.settings import PROJECT_ROOT

I18N_DIR = PROJECT_ROOT / "config" / "i18n"
DEFAULT_LANG = "en"
LANG_COOKIE = "lang"


def _load(lang: str) -> dict[str, Any]:
    return yaml.safe_load((I18N_DIR / f"{lang}.yaml").read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=8)
def available_langs() -> tuple[str, ...]:
    """Every config/i18n/<lang>.yaml on disk, English first."""
    langs = sorted(p.stem for p in I18N_DIR.glob("*.yaml"))
    return tuple([DEFAULT_LANG, *(x for x in langs if x != DEFAULT_LANG)])


def _deep_fill(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """`over` wins; keys it is missing fall back to `base`, so a half-translated
    file degrades to English instead of showing a raw key."""
    out = dict(base)
    for k, v in over.items():
        nested = isinstance(v, dict) and isinstance(base.get(k), dict)
        out[k] = _deep_fill(base[k], v) if nested else v
    return out


@lru_cache(maxsize=8)
def strings(lang: str) -> dict[str, Any]:
    lang = normalize(lang)
    en = _load(DEFAULT_LANG)
    return en if lang == DEFAULT_LANG else _deep_fill(en, _load(lang))


def match(tag: str | None) -> str | None:
    """The language we ship that `tag` asks for, or None if it asks for none of them.

    Distinguishing "asked for English" from "asked for nothing we have" is what lets
    a cookie of `en` stop the browser's Accept-Language from overriding it.
    """
    if not tag:
        return None
    t = tag.strip().lower().replace("_", "-")
    if t in available_langs():
        return t
    base = t.split("-")[0]  # zh-CN, zh-Hans-CN -> zh
    return base if base in available_langs() else None


def normalize(lang: str | None) -> str:
    """Same as `match`, but anything unknown becomes the default."""
    return match(lang) or DEFAULT_LANG


def resolve(query: str | None, cookie: str | None, accept_language: str | None = None) -> str:
    """An explicit choice beats a remembered one, which beats the browser's preference.

    `?lang=` comes first so a demo link can pin the language without touching the cookie.
    """
    for candidate in (query, cookie):
        if (picked := match(candidate)) is not None:
            return picked
    for part in (accept_language or "").split(","):
        if (picked := match(part.split(";")[0])) is not None:
            return picked
    return DEFAULT_LANG
