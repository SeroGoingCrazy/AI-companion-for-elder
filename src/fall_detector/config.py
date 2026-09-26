"""Load the `fall:` section of config/settings.yaml.

fall_detector never imports elder_companion (spec 5.3), so this is a small standalone reader
with the same ${ENV} / ${ENV:-default} expansion.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class RuleConfig(_Model):
    """Thresholds for the fall state machine (spec 3.5)."""

    min_keypoint_conf: float = 0.3
    aspect_ratio_threshold: float = 1.2
    torso_angle_deg: float = 60
    drop_ratio: float = 0.35
    drop_window_s: float = 0.6
    down_confirm_s: float = 3
    cooldown_s: float = 30


class MCPConfig(_Model):
    host: str = "127.0.0.1"
    port: int = 8001
    events_path: Path = Path("data/fall_events.jsonl")
    snapshots_dir: Path = Path("data/snapshots")
    autostart_source: str | None = None

    @field_validator("events_path", "snapshots_dir", mode="after")
    @classmethod
    def _resolve(cls, v: Path) -> Path:
        return v if v.is_absolute() else PROJECT_ROOT / v

    @field_validator("autostart_source", mode="before")
    @classmethod
    def _empty_to_none(cls, v: Any) -> Any:
        return v or None


class FallConfig(_Model):
    model: str = "models/yolo11n-pose.pt"
    device: str = "cpu"
    vision_verify: bool = False
    report_url: str = "http://localhost:8000/api/alerts"
    rules: RuleConfig = RuleConfig()
    mcp: MCPConfig = MCPConfig()

    @property
    def model_path(self) -> str:
        """Local weights resolve against the project root; a bare name lets ultralytics fetch it."""
        p = Path(self.model)
        if p.is_absolute() or len(p.parts) == 1:
            return str(p)
        return str(PROJECT_ROOT / p)


def _expand(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand(v) for v in obj]
    if isinstance(obj, str):
        return _ENV_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), m.group(2) if m.group(2) is not None else ""), obj
        )
    return obj


def load_config(path: Path | None = None, *, load_env_file: bool = True) -> FallConfig:
    if load_env_file:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    path = path or Path(os.environ.get("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    fall = _expand(raw.get("fall") or {})
    rule_keys = RuleConfig.model_fields.keys()
    fall["rules"] = {k: fall.pop(k) for k in list(fall) if k in rule_keys}
    return FallConfig.model_validate(fall)
