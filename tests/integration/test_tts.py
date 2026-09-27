from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from elder_companion.llm.mock import SILENT_MP3, MockLLMClient
from elder_companion.models import Message
from elder_companion.settings import Settings
from elder_companion.web.app import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def mock_llm() -> MockLLMClient:
    cfg = MockLLMClient.from_config().config
    return MockLLMClient({**cfg, "tts": {"available": True}})


@pytest.fixture
def client(settings: Settings, mock_llm: MockLLMClient) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=mock_llm)) as c:
        yield c


def _assistant_id(c: TestClient) -> int:
    return c.post("/api/chat", json={"text": "I didn't sleep well"}).json()["message_id"]


def _tts_calls(llm: MockLLMClient) -> int:
    return sum(1 for name, _ in llm.calls if name == "tts")


def test_tts_generates_mp3_once_then_caches(client, mock_llm, settings: Settings) -> None:
    mid = _assistant_id(client)

    r = client.get(f"/api/tts/{mid}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == SILENT_MP3
    assert (settings.paths.audio_dir / f"{mid}.mp3").exists()

    assert client.get(f"/api/tts/{mid}").status_code == 200
    assert _tts_calls(mock_llm) == 1  # second request served from cache

    with client.app.state.sessionmaker() as s:
        assert s.get(Message, mid).audio_path == f"audio/{mid}.mp3"


def test_tts_speaks_the_reply_text(client, mock_llm) -> None:
    mid = _assistant_id(client)
    client.get(f"/api/tts/{mid}")
    spoken = [arg for name, arg in mock_llm.calls if name == "tts"][0]
    with client.app.state.sessionmaker() as s:
        assert spoken == s.get(Message, mid).text


def test_tts_unavailable_returns_204(settings: Settings) -> None:
    llm = MockLLMClient.from_config()  # repo config: tts.available = false
    with TestClient(create_app(settings, llm=llm)) as c:
        mid = _assistant_id(c)
        r = c.get(f"/api/tts/{mid}")
        assert r.status_code == 204
        assert r.content == b""


def test_tts_rejects_user_and_unknown_messages(client: TestClient) -> None:
    mid = _assistant_id(client)
    user_mid = mid - 1
    assert client.get(f"/api/tts/{user_mid}").status_code == 404
    assert client.get("/api/tts/9999").status_code == 404


def test_tts_response_revalidates_instead_of_caching(client: TestClient) -> None:
    mid = _assistant_id(client)
    r = client.get(f"/api/tts/{mid}")
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers.get("etag")
    again = client.get(f"/api/tts/{mid}", headers={"if-none-match": r.headers["etag"]})
    assert again.status_code == 304


def test_tts_voice_switch_caches_per_voice(client, mock_llm, settings: Settings) -> None:
    mid = _assistant_id(client)
    assert client.get(f"/api/tts/{mid}?voice=male").status_code == 200
    assert client.get(f"/api/tts/{mid}?voice=female").status_code == 200
    assert client.get(f"/api/tts/{mid}?voice=male").status_code == 200  # cached
    assert _tts_calls(mock_llm) == 2
    male, female = settings.llm.tts_voices["male"], settings.llm.tts_voices["female"]
    assert (settings.paths.audio_dir / f"{mid}.{male}.mp3").exists()
    assert (settings.paths.audio_dir / f"{mid}.{female}.mp3").exists()


def test_tts_rejects_unknown_voice(client: TestClient) -> None:
    mid = _assistant_id(client)
    assert client.get(f"/api/tts/{mid}?voice=robot").status_code == 422


def test_elder_page_offers_the_voice_switch(client: TestClient) -> None:
    html = client.get("/elder").text
    assert 'id="voice-switch"' in html
    assert '"voices": ["female", "male"]' in html
