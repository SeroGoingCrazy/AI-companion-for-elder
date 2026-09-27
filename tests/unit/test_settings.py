from pathlib import Path

import pytest
import yaml

from elder_companion.settings import (
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT,
    SettingsError,
    expand_env,
    load_settings,
)

pytestmark = pytest.mark.unit


_ENV_VARS = (
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "CHAT_MODEL",
    "DATABASE_URL",
    "DATA_DIR",
    "PORT",
    "ELDER_TIMEZONE",
    "COMPANION_NAME",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "settings.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _base() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def test_repo_settings_load_in_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    s = load_settings(load_env_file=False)
    assert s.llm.provider == "mock"
    assert s.llm.api_key is None
    assert s.server.port == 8000
    assert s.paths.data_dir == PROJECT_ROOT / "data"
    assert s.paths.snapshots_dir == PROJECT_ROOT / "data" / "snapshots"


def test_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("CHAT_MODEL", "my-model")
    monkeypatch.setenv("PORT", "9000")
    s = load_settings(load_env_file=False)
    assert s.llm.chat_model == "my-model"
    assert s.server.port == 9000


def test_openai_provider_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SettingsError, match="OPENAI_API_KEY is required"):
        load_settings(load_env_file=False)


def test_openai_provider_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert load_settings(load_env_file=False).llm.api_key == "test-key"


def test_missing_env_without_default_names_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SettingsError, match="NOT_SET_ANYWHERE"):
        expand_env({"llm": {"api_key": "${NOT_SET_ANYWHERE}"}})


def test_literal_api_key_rejected(tmp_path: Path) -> None:
    data = _base()
    data["llm"]["api_key"] = "sk-abcdefghijklmnop"
    with pytest.raises(SettingsError, match="llm.api_key: API keys must come from environment"):
        load_settings(_write(tmp_path, data), load_env_file=False)


def test_missing_field_reports_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    data = _base()
    del data["llm"]["chat_model"]
    with pytest.raises(SettingsError, match="llm.chat_model is required"):
        load_settings(_write(tmp_path, data), load_env_file=False)


def test_unknown_provider_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    with pytest.raises(SettingsError, match="llm.provider"):
        load_settings(load_env_file=False)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="not found"):
        load_settings(tmp_path / "nope.yaml", load_env_file=False)


def test_invalid_timezone_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    data = _base()
    data["chat"]["timezone"] = "Mars/Olympus_Mons"
    with pytest.raises(SettingsError, match="chat.timezone unknown timezone"):
        load_settings(_write(tmp_path, data), load_env_file=False)


def test_chat_timezone_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    s = load_settings(load_env_file=False)
    assert s.chat.tz.key == "America/Los_Angeles"
    assert s.chat.companion_name == "Hallo"
