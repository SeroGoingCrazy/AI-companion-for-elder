"""Thin LLM client interface over the OpenAI SDK, plus a factory that picks openai or mock."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import openai

from elder_companion.settings import LLMSettings

logger = logging.getLogger(__name__)

ChatMessage = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": ...}

_NON_WORD = re.compile(r"[\W_]+")
MIN_ECHO_CHARS = 6


def is_prompt_echo(transcript: str, prompt: str) -> bool:
    """True when a transcript is just (part of) the ASR prompt, which happens on silent clips."""
    t = _NON_WORD.sub("", transcript.lower())
    p = _NON_WORD.sub("", prompt.lower())
    return len(t) >= MIN_ECHO_CHARS and t in p


class LLMError(RuntimeError):
    """Any failure talking to the model: network, timeout, refusal, empty or malformed output."""


class BaseLLMClient(ABC):
    @abstractmethod
    def chat(self, messages: list[ChatMessage], *, max_tokens: int | None = None) -> str:
        """Return the assistant reply text."""

    @abstractmethod
    def extract_json(
        self, messages: list[ChatMessage], *, schema: dict[str, Any], name: str
    ) -> dict[str, Any]:
        """Return a dict conforming to `schema` (strict structured output)."""

    @abstractmethod
    def transcribe(self, audio: bytes, *, filename: str, language: str | None = None) -> str:
        """Return the transcript of an audio clip."""

    @abstractmethod
    def tts(self, text: str, *, voice: str | None = None) -> bytes:
        """Return mp3 audio for `text` (voice: a provider voice name; None = the default)."""


class OpenAIClient(BaseLLMClient):
    # temperature is intentionally never sent: some newer models reject it.

    def __init__(self, settings: LLMSettings, client: openai.OpenAI | None = None) -> None:
        self._s = settings
        self._client = client or openai.OpenAI(
            api_key=settings.api_key, timeout=settings.timeout_s, max_retries=1
        )

    def chat(self, messages: list[ChatMessage], *, max_tokens: int | None = None) -> str:
        kwargs: dict[str, Any] = {"model": self._s.chat_model, "messages": messages}
        if max_tokens:
            kwargs["max_completion_tokens"] = max_tokens
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except openai.OpenAIError as e:
            raise LLMError(f"chat failed: {e}") from e
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise LLMError("chat returned an empty reply")
        return text

    def extract_json(
        self, messages: list[ChatMessage], *, schema: dict[str, Any], name: str
    ) -> dict[str, Any]:
        try:
            resp = self._client.chat.completions.create(
                model=self._s.extract_model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": name, "schema": schema, "strict": True},
                },
            )
        except openai.OpenAIError as e:
            raise LLMError(f"extract_json failed: {e}") from e
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            raise LLMError(f"model refused: {msg.refusal}")
        try:
            return json.loads(msg.content or "")
        except json.JSONDecodeError as e:
            raise LLMError(f"model returned invalid JSON: {e}") from e

    def transcribe(self, audio: bytes, *, filename: str, language: str | None = None) -> str:
        kwargs: dict[str, Any] = {"model": self._s.asr_model, "file": (filename, audio)}
        if language:
            kwargs["language"] = language
        if self._s.asr_prompt:
            kwargs["prompt"] = self._s.asr_prompt
        try:
            resp = self._client.audio.transcriptions.create(**kwargs)
        except openai.OpenAIError as e:
            raise LLMError(f"transcribe failed: {e}") from e
        text = (resp.text or "").strip()
        if self._s.asr_prompt and is_prompt_echo(text, self._s.asr_prompt):
            logger.info("discarding transcript that echoes the ASR prompt: %r", text)
            return ""
        return text

    def tts(self, text: str, *, voice: str | None = None) -> bytes:
        try:
            resp = self._client.audio.speech.create(
                model=self._s.tts_model,
                voice=voice or self._s.tts_voice,
                input=text,
                instructions=self._s.tts_instructions,
                response_format="mp3",
            )
        except openai.OpenAIError as e:
            raise LLMError(f"tts failed: {e}") from e
        return resp.read()


def get_llm(settings: LLMSettings) -> BaseLLMClient:
    if settings.provider == "mock":
        from elder_companion.llm.mock import MockLLMClient

        return MockLLMClient.from_config()
    return OpenAIClient(settings)
