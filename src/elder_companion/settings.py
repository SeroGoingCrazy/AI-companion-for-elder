"""Load config/settings.yaml with ${ENV} / ${ENV:-default} expansion into frozen models."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class SettingsError(ValueError):
    """Raised when settings cannot be loaded or are invalid."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LLMSettings(_Model):
    provider: Literal["openai", "mock"]
    api_key: str | None = None
    chat_model: str
    extract_model: str
    asr_model: str
    asr_prompt: str = ""  # biases transcription style, e.g. Simplified Chinese
    tts_model: str
    tts_voice: str
    # Voices the elder can pick in the app (key -> provider voice name).
    tts_voices: dict[str, str] = {"female": "coral", "male": "ash"}
    tts_instructions: str
    vision_model: str
    timeout_s: float = 15

    @field_validator("api_key", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        return v or None

    @model_validator(mode="after")
    def _require_key_for_openai(self) -> LLMSettings:
        if self.provider == "openai" and not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when llm.provider=openai "
                "(set LLM_PROVIDER=mock to run offline)"
            )
        return self


class ChatSettings(_Model):
    companion_name: str = "Sunny"
    timezone: str = "America/Los_Angeles"  # the elder's local time (greetings, "yesterday")
    history_turns: int = 10
    max_reply_tokens: int = 150
    follow_up_hours: float = 48  # unresolved symptoms this recent are offered for follow-up

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone {v!r}") from e
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class SymptomSettings(_Model):
    merge_window_hours: float = 24
    alert_debounce_hours: float = 2


class MemorySettings(_Model):
    care_list_min_mentions: int = 2
    follow_up_default_delay_days: int = 1
    follow_up_expire_days: int = 7


class AgendaSettings(_Model):
    max_items_per_greet: int = 3


class PrivacySettings(_Model):
    max_span_user_messages: int = 3  # preceding user messages a privacy request can cover
    bypass_levels: tuple[str, ...] = ("high",)  # alert levels that ignore privacy (ADR 19)
    # Hold symptoms and personal matters back from the family until she agrees (ADR 23).
    ask_before_sharing: bool = True


class FallMCPSettings(_Model):
    host: str = "127.0.0.1"
    port: int = 8001
    events_path: str
    snapshots_dir: str
    autostart_source: str | None = None

    @field_validator("autostart_source", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        return v or None


class FallSettings(_Model):
    model: str = "models/yolo11n-pose.pt"
    device: str = "cpu"
    min_keypoint_conf: float = 0.3
    aspect_ratio_threshold: float
    torso_angle_deg: float
    drop_ratio: float
    drop_window_s: float
    down_confirm_s: float
    cooldown_s: float
    vision_verify: bool = False
    report_url: str
    mcp: FallMCPSettings


class DatabaseSettings(_Model):
    url: str


class ServerSettings(_Model):
    host: str = "127.0.0.1"
    port: int = 8000


class PathSettings(_Model):
    data_dir: Path

    @field_validator("data_dir", mode="after")
    @classmethod
    def _resolve(cls, v: Path) -> Path:
        return v if v.is_absolute() else PROJECT_ROOT / v

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"


class Settings(_Model):
    llm: LLMSettings
    chat: ChatSettings
    symptoms: SymptomSettings
    memory: MemorySettings = MemorySettings()
    agenda: AgendaSettings = AgendaSettings()
    privacy: PrivacySettings = PrivacySettings()
    fall: FallSettings
    database: DatabaseSettings
    server: ServerSettings
    paths: PathSettings


def expand_env(obj: Any, path: str = "") -> Any:
    """Recursively replace ${VAR} / ${VAR:-default} in strings; unset VAR without default fails."""
    if isinstance(obj, dict):
        return {k: expand_env(v, f"{path}.{k}" if path else str(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    if isinstance(obj, str):
        if obj.startswith("sk-"):
            raise SettingsError(
                f"{path}: API keys must come from environment variables, not settings.yaml"
            )

        def _sub(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            value = os.environ.get(name)
            if value is not None:
                return value
            if default is not None:
                return default
            raise SettingsError(f"{path}: environment variable {name} is not set")

        return _ENV_PATTERN.sub(_sub, obj)
    return obj


def _format_validation_error(err: ValidationError) -> str:
    parts = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        msg = "is required" if e["type"] == "missing" else e["msg"].removeprefix("Value error, ")
        parts.append(f"{loc} {msg}" if loc else msg)
    return "; ".join(parts)


def load_settings(path: Path | None = None, *, load_env_file: bool = True) -> Settings:
    if load_env_file:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    path = path or Path(os.environ.get("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as e:
        raise SettingsError(f"settings file not found: {path}") from e
    try:
        return Settings.model_validate(expand_env(raw))
    except ValidationError as e:
        raise SettingsError(_format_validation_error(e)) from None


@lru_cache
def get_settings() -> Settings:
    return load_settings()
