from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from elder_companion.llm import LLMError
from elder_companion.llm.mock import MOCK_AUDIO_PREFIX, MockLLMClient
from elder_companion.models import Message
from elder_companion.settings import Settings
from elder_companion.web.app import create_app
from elder_companion.web.routes.chat import MAX_AUDIO_BYTES, audio_filename

pytestmark = pytest.mark.integration


class DeafLLM(MockLLMClient):
    def transcribe(self, audio, *, filename, language=None):  # noqa: ANN001
        raise LLMError("asr down")


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient.from_config()


@pytest.fixture
def client(settings: Settings, mock_llm: MockLLMClient) -> Iterator[TestClient]:
    with TestClient(create_app(settings, llm=mock_llm)) as c:
        yield c


def _post_audio(c: TestClient, data: bytes, content_type: str = "audio/webm;codecs=opus", **form):
    return c.post("/api/chat/audio", files={"audio": ("blob", data, content_type)}, data=form)


def _roles(c: TestClient) -> list[str]:
    with c.app.state.sessionmaker() as s:
        return [m.role for m in s.scalars(select(Message).order_by(Message.id))]


def test_voice_turn_transcribes_then_replies(client: TestClient) -> None:
    r = _post_audio(client, MOCK_AUDIO_PREFIX + "这两天早上起来头有点晕".encode())
    assert r.status_code == 200
    body = r.json()
    assert body["need_retry"] is False
    assert body["user_text"] == "这两天早上起来头有点晕"
    assert "头晕" in body["reply_text"]
    assert isinstance(body["message_id"], int)
    assert _roles(client) == ["user", "assistant"]


def test_filename_extension_follows_content_type(client, mock_llm) -> None:
    _post_audio(client, MOCK_AUDIO_PREFIX + b"hello there", content_type="audio/mp4")
    assert ("transcribe", "speech.mp4") in mock_llm.calls


@pytest.mark.parametrize("transcript", [b"", b"   ", b"a"])
def test_unintelligible_audio_asks_to_retry(client: TestClient, transcript: bytes) -> None:
    body = _post_audio(client, MOCK_AUDIO_PREFIX + transcript).json()
    assert body == {
        "message_id": None,
        "user_text": "",
        "reply_text": "",
        "fallback": False,
        "need_retry": True,
    }
    assert _roles(client) == []  # nothing saved


def test_transcription_failure_asks_to_retry(settings: Settings) -> None:
    with TestClient(create_app(settings, llm=DeafLLM({}))) as c:
        body = _post_audio(c, b"\x1a\x45\xdf\xa3 real audio").json()
        assert body["need_retry"] is True
        assert _roles(c) == []


def test_empty_upload_422(client: TestClient) -> None:
    assert _post_audio(client, b"").status_code == 422


def test_missing_file_422(client: TestClient) -> None:
    assert client.post("/api/chat/audio", data={"elder_id": "1"}).status_code == 422


def test_oversized_upload_413(client: TestClient) -> None:
    assert _post_audio(client, b"\x00" * (MAX_AUDIO_BYTES + 1)).status_code == 413


def test_unknown_elder_404_before_transcribing(client, mock_llm) -> None:
    r = _post_audio(client, MOCK_AUDIO_PREFIX + b"hello", elder_id="99")
    assert r.status_code == 404
    assert not [c for c in mock_llm.calls if c[0] == "transcribe"]


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("audio/webm;codecs=opus", "speech.webm"),
        ("audio/mp4", "speech.mp4"),
        ("AUDIO/WAV", "speech.wav"),
        ("audio/mpeg", "speech.mp3"),
        (None, "speech.webm"),
        ("application/octet-stream", "speech.webm"),
    ],
)
def test_audio_filename(content_type, expected) -> None:
    assert audio_filename(content_type) == expected
