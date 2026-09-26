from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from elder_companion.settings import Settings, load_settings
from elder_companion.web.app import create_app


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Offline settings: mock LLM, throwaway sqlite + data dir under tmp_path."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return load_settings(load_env_file=False)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c
