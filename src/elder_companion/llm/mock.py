"""Offline LLM for tests and as a demo fallback (LLM_PROVIDER=mock).

Replies come from config/mock_llm.yaml.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from elder_companion.llm.client import BaseLLMClient, ChatMessage, LLMError
from elder_companion.settings import PROJECT_ROOT

DEFAULT_MOCK_CONFIG = PROJECT_ROOT / "config" / "mock_llm.yaml"

# Transcribe shortcut for tests and scripted demos: audio bytes b"MOCK:<text>" transcribe to <text>.
MOCK_AUDIO_PREFIX = b"MOCK:"

# One silent MPEG-1 Layer III frame (128 kbps, 44.1 kHz); a few of them make a valid tiny mp3.
_SILENT_MP3_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413
SILENT_MP3 = _SILENT_MP3_FRAME * 4

_CJK = re.compile(r"[一-鿿]")
# Memory extraction puts the utterance under "Current utterance [#<id>]:". Results may use the
# placeholder below where the real model would give that id (privacy_request.covers_message_ids).
_CURRENT_ID = re.compile(r"\[#(\d+)\]:?\s*\n[^\n]*\Z")
CURRENT_MESSAGE_ID = "$current_message_id"


def _fill_current_id(value: Any, current_id: int | None) -> Any:
    if isinstance(value, dict):
        return {k: _fill_current_id(v, current_id) for k, v in value.items()}
    if isinstance(value, list):
        return [
            _fill_current_id(v, current_id)
            for v in value
            if not (v == CURRENT_MESSAGE_ID and current_id is None)
        ]
    return current_id if value == CURRENT_MESSAGE_ID else value


def _match(text: str, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    lowered = text.lower()
    for rule in rules:
        if any(k.lower() in lowered for k in rule.get("match", [])):
            return rule
    return None


@dataclass
class MockLLMClient(BaseLLMClient):
    config: dict[str, Any]
    calls: list[tuple[str, Any]] = field(default_factory=list)

    @classmethod
    def from_config(cls, path: Path = DEFAULT_MOCK_CONFIG) -> MockLLMClient:
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def chat(self, messages: list[ChatMessage], *, max_tokens: int | None = None) -> str:
        self.calls.append(("chat", messages))
        cfg = self.config.get("chat", {})
        # A conversation ending in a system instruction is a greeting request; greet_rules
        # match that instruction (e.g. a follow-up on the agenda).
        if messages and messages[-1]["role"] == "system":
            rule = _match(messages[-1]["content"], cfg.get("greet_rules", []))
            return rule["reply"] if rule else cfg.get("greet", "Hello! How are you today?")
        # system_rules match the first system prompt (e.g. the daily summary's instructions).
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        rule = _match(system, cfg.get("system_rules", []))
        if rule:
            return rule["reply"]
        user_text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        rule = _match(user_text, cfg.get("rules", []))
        if rule:
            return rule["reply"]
        if _CJK.search(user_text):
            return cfg.get("default_zh", cfg.get("default", "嗯，我在听。"))
        return cfg.get("default", "I'm listening.")

    def extract_json(
        self, messages: list[ChatMessage], *, schema: dict[str, Any], name: str
    ) -> dict[str, Any]:
        self.calls.append(("extract_json", {"name": name, "messages": messages}))
        cfg = self.config.get("json", {}).get(name, {})
        user_text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        # Match only the last paragraph: extraction puts earlier turns before the utterance,
        # and a symptom mentioned back there must not fire its rule again.
        last = user_text.rsplit("\n\n", 1)[-1]
        rule = _match(last, cfg.get("rules", []))
        result = copy.deepcopy(rule["result"] if rule else cfg.get("default", {}))
        m = _CURRENT_ID.search(last)
        return _fill_current_id(result, int(m.group(1)) if m else None)

    def transcribe(self, audio: bytes, *, filename: str, language: str | None = None) -> str:
        self.calls.append(("transcribe", filename))
        if audio.startswith(MOCK_AUDIO_PREFIX):
            return audio[len(MOCK_AUDIO_PREFIX) :].decode("utf-8").strip()
        return self.config.get("transcribe", {}).get("default", "")

    def tts(self, text: str) -> bytes:
        self.calls.append(("tts", text))
        # available: false makes the app answer 204, so the browser speaks with its own voice.
        if not self.config.get("tts", {}).get("available", True):
            raise LLMError("mock TTS disabled")
        return SILENT_MP3
