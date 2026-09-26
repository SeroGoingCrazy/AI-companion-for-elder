import json

import httpx
import openai
import pytest

from elder_companion.llm import LLMError, OpenAIClient, get_llm
from elder_companion.llm.mock import MOCK_AUDIO_PREFIX, SILENT_MP3, MockLLMClient
from elder_companion.settings import LLMSettings

pytestmark = pytest.mark.unit

MOCK_CFG = {
    "chat": {
        "greet": "Good morning!",
        "default": "I'm listening.",
        "default_zh": "我在听。",
        "rules": [{"match": ["Dizzy", "头晕"], "reply": "Oh no, dizzy?"}],
    },
    "json": {
        "demo": {
            "default": {"items": []},
            "rules": [{"match": ["knee"], "result": {"items": ["knee"]}}],
        }
    },
    "transcribe": {"default": "hello there"},
}


def _llm_settings(**kw) -> LLMSettings:
    base = dict(
        provider="openai",
        api_key="test-key",
        chat_model="chat-m",
        extract_model="extract-m",
        asr_model="asr-m",
        tts_model="tts-m",
        tts_voice="coral",
        tts_instructions="slowly",
        vision_model="vision-m",
    )
    return LLMSettings(**{**base, **kw})


# ---- MockLLMClient ----


def test_mock_chat_rule_is_case_insensitive() -> None:
    m = MockLLMClient(MOCK_CFG)
    assert m.chat([{"role": "user", "content": "I feel dizzy today"}]) == "Oh no, dizzy?"
    assert m.chat([{"role": "user", "content": "早上起来头晕"}]) == "Oh no, dizzy?"


def test_mock_chat_defaults_follow_language() -> None:
    m = MockLLMClient(MOCK_CFG)
    assert m.chat([{"role": "user", "content": "nice weather"}]) == "I'm listening."
    assert m.chat([{"role": "user", "content": "今天天气不错"}]) == "我在听。"


def test_mock_chat_trailing_system_message_is_a_greeting() -> None:
    m = MockLLMClient(MOCK_CFG)
    msgs = [{"role": "system", "content": "persona"}, {"role": "system", "content": "greet now"}]
    assert m.chat(msgs) == "Good morning!"


def test_mock_extract_json_rules_and_default_are_copies() -> None:
    m = MockLLMClient(MOCK_CFG)
    hit = m.extract_json([{"role": "user", "content": "my knee"}], schema={}, name="demo")
    assert hit == {"items": ["knee"]}
    hit["items"].append("mutated")
    miss = m.extract_json([{"role": "user", "content": "fine"}], schema={}, name="demo")
    assert miss == {"items": []}
    again = m.extract_json([{"role": "user", "content": "my knee"}], schema={}, name="demo")
    assert again == {"items": ["knee"]}


def test_mock_transcribe_prefix_and_default() -> None:
    m = MockLLMClient(MOCK_CFG)
    assert m.transcribe(MOCK_AUDIO_PREFIX + "头有点晕".encode(), filename="a.webm") == "头有点晕"
    assert m.transcribe(b"\x1a\x45\xdf\xa3", filename="a.webm") == "hello there"


def test_mock_tts_returns_mp3_and_records_calls() -> None:
    m = MockLLMClient(MOCK_CFG)
    audio = m.tts("hi")
    assert audio == SILENT_MP3 and audio.startswith(b"\xff\xfb")
    assert m.calls == [("tts", "hi")]


def test_repo_mock_config_loads_and_covers_demo_lines() -> None:
    m = MockLLMClient.from_config()
    line = "My chest feels tight and I can't catch my breath"
    reply = m.chat([{"role": "user", "content": line}])
    assert "911" in reply


def test_get_llm_picks_provider() -> None:
    assert isinstance(get_llm(_llm_settings(provider="mock", api_key=None)), MockLLMClient)
    assert isinstance(get_llm(_llm_settings()), OpenAIClient)


# ---- OpenAIClient against a fake HTTP transport ----


def _client(handler) -> tuple[OpenAIClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    sdk = openai.OpenAI(
        api_key="test-key",
        base_url="https://fake.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(_record)),
    )
    return OpenAIClient(_llm_settings(), client=sdk), seen


def _completion(content: str | None, refusal: str | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content, "refusal": refusal},
                }
            ],
        },
    )


def test_openai_chat_sends_model_and_token_cap() -> None:
    client, seen = _client(lambda r: _completion("  Hi Maggie!  "))
    assert client.chat([{"role": "user", "content": "hi"}], max_tokens=50) == "Hi Maggie!"
    body = json.loads(seen[0].content)
    assert body["model"] == "chat-m"
    assert body["max_completion_tokens"] == 50
    assert "temperature" not in body


def test_openai_chat_empty_reply_is_error() -> None:
    client, _ = _client(lambda r: _completion(""))
    with pytest.raises(LLMError, match="empty"):
        client.chat([{"role": "user", "content": "hi"}])


def test_openai_http_error_maps_to_llm_error() -> None:
    client, _ = _client(lambda r: httpx.Response(500, json={"error": {"message": "boom"}}))
    with pytest.raises(LLMError, match="chat failed"):
        client.chat([{"role": "user", "content": "hi"}])


def test_openai_extract_json_uses_strict_schema() -> None:
    client, seen = _client(lambda r: _completion('{"items": ["knee"]}'))
    schema = {"type": "object", "properties": {"items": {"type": "array"}}}
    out = client.extract_json([{"role": "user", "content": "x"}], schema=schema, name="demo")
    assert out == {"items": ["knee"]}
    body = json.loads(seen[0].content)
    assert body["model"] == "extract-m"
    expected = {"name": "demo", "schema": schema, "strict": True}
    assert body["response_format"]["json_schema"] == expected


@pytest.mark.parametrize(
    ("content", "refusal", "match"),
    [("not json", None, "invalid JSON"), (None, "I can't help", "refused")],
)
def test_openai_extract_json_failures(content, refusal, match) -> None:
    client, _ = _client(lambda r: _completion(content, refusal))
    with pytest.raises(LLMError, match=match):
        client.extract_json([{"role": "user", "content": "x"}], schema={}, name="demo")


def test_openai_transcribe_and_tts() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": " hello "})
        return httpx.Response(200, content=b"MP3DATA", headers={"content-type": "audio/mpeg"})

    client, seen = _client(handler)
    assert client.transcribe(b"audio", filename="a.webm", language="en") == "hello"
    assert client.tts("hi") == b"MP3DATA"
    tts_body = json.loads(seen[1].content)
    assert tts_body["model"] == "tts-m"
    assert tts_body["voice"] == "coral"
    assert tts_body["instructions"] == "slowly"
