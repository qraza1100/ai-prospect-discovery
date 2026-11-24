"""Request and response handling of the third-party API clients, against mocked transports."""

import json

import anthropic
import httpx
import httpx2
import pytest

from app.config import Settings
from app.container import build_container
from app.errors import LLMError, UpstreamError
from app.services.llm_service import AnthropicLLM, FallbackLLM, GeminiLLM, OpenAILLM, build_llm
from app.services.search_service import BraveBackend, SerperBackend
from tests.conftest import FakeLLM

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_serper_request_and_parsing():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["x-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Jane &amp; Co | LinkedIn",
                        "link": "https://www.linkedin.com/in/jane",
                        "snippet": "<b>DC</b> fan",
                        "position": 1,
                    },
                    {"title": "no link"},
                ]
            },
        )

    results = await SerperBackend("k1", mock_client(handler), 5).search("q", country_code="US", num=10)
    assert seen == {"key": "k1", "body": {"q": "q", "num": 10, "hl": "en", "gl": "us"}}
    assert len(results) == 1
    assert results[0].title == "Jane & Co | LinkedIn" and results[0].snippet == "DC fan"


async def test_brave_request_and_parsing():
    def handler(request):
        assert request.headers["x-subscription-token"] == "k2"
        assert request.url.params["country"] == "GB"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "url": "https://x.com/jane",
                            "title": "Jane",
                            "description": "London <strong>founder</strong>",
                            "extra_snippets": ["Startup builder"],
                        },
                    ]
                }
            },
        )

    [hit] = await BraveBackend("k2", mock_client(handler), 5).search("q", country_code="gb", num=30)
    assert hit.snippet == "London founder Startup builder"


async def test_search_backend_errors_are_typed():
    backend = SerperBackend("bad", mock_client(lambda r: httpx.Response(403)), 5)
    with pytest.raises(UpstreamError):
        await backend.search("q", country_code=None, num=10)


async def test_gemini_client_sends_schema_and_parses_text():
    def handler(request):
        body = json.loads(request.content)
        assert request.headers["x-goog-api-key"] == "g"
        assert body["generationConfig"]["responseJsonSchema"] == SCHEMA
        assert body["systemInstruction"]["parts"][0]["text"] == "sys"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [{"text": "thinking", "thought": True}, {"text": '{"ok": true}'}]
                        },
                    }
                ]
            },
        )

    llm = GeminiLLM("g", "gemini-3.1-flash-lite", mock_client(handler), 5)
    assert await llm.generate_json(system="sys", prompt="p", schema=SCHEMA, max_tokens=50) == {"ok": True}


async def test_gemini_truncation_is_an_error():
    def handler(request):
        return httpx.Response(
            200, json={"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]}
        )

    with pytest.raises(LLMError):
        await GeminiLLM("g", "m", mock_client(handler), 5).generate_json(
            system="s", prompt="p", schema=SCHEMA, max_tokens=5
        )


async def test_openai_client_uses_strict_json_schema():
    def handler(request):
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer o"
        assert body["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok": false}'}}]}
        )

    llm = OpenAILLM("o", "gpt-5.4-nano", mock_client(handler), 5)
    assert await llm.generate_json(system="s", prompt="p", schema=SCHEMA, max_tokens=50) == {"ok": False}


async def test_openai_refusal_and_bad_json_are_errors():
    refusal = mock_client(lambda r: httpx.Response(200, json={"choices": [{"message": {"refusal": "no"}}]}))
    with pytest.raises(LLMError):
        await OpenAILLM("o", "m", refusal, 5).generate_json(
            system="s", prompt="p", schema=SCHEMA, max_tokens=5
        )
    garbage = mock_client(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
    )
    with pytest.raises(LLMError):
        await OpenAILLM("o", "m", garbage, 5).generate_json(
            system="s", prompt="p", schema=SCHEMA, max_tokens=5
        )


def anthropic_llm(handler, effort="low"):
    http_client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return AnthropicLLM("a", "claude-haiku-5-5", 5, effort, http_client=http_client)


def anthropic_message(text, stop_reason="end_turn"):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


async def test_anthropic_client_uses_structured_output():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx2.Response(200, json=anthropic_message('{"ok": true}'))

    llm = anthropic_llm(handler)
    assert await llm.generate_json(system="sys", prompt="p", schema=SCHEMA, max_tokens=64) == {"ok": True}
    assert seen["model"] == "claude-haiku-5-5"
    assert seen["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"}
    assert seen["system"] == "sys"
    assert "temperature" not in seen
    await llm.aclose()


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_anthropic_incomplete_answers_are_errors(stop_reason):
    llm = anthropic_llm(lambda r: httpx2.Response(200, json=anthropic_message("{", stop_reason)))
    with pytest.raises(LLMError):
        await llm.generate_json(system="s", prompt="p", schema=SCHEMA, max_tokens=5)


async def test_anthropic_http_errors_are_wrapped():
    llm = anthropic_llm(
        lambda r: httpx2.Response(
            400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
        )
    )
    with pytest.raises(LLMError):
        await llm.generate_json(system="s", prompt="p", schema=SCHEMA, max_tokens=5)


async def test_fallback_chain_uses_second_client():
    def broken(system, payload):
        raise LLMError("down")

    chain = FallbackLLM([FakeLLM(broken), FakeLLM(lambda s, p: {"ok": True})])
    assert await chain.generate_json(system="s", prompt="{}", schema=SCHEMA, max_tokens=5) == {"ok": True}
    with pytest.raises(LLMError):
        await FallbackLLM([FakeLLM(broken)]).generate_json(
            system="s", prompt="{}", schema=SCHEMA, max_tokens=5
        )


def test_build_llm_skips_providers_without_keys():
    client = httpx.AsyncClient()
    none = Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key=None)
    assert build_llm(none, client) is None
    chain = Settings(
        _env_file=None,
        llm_provider="gemini",
        gemini_api_key="g",
        llm_fallback_provider="openai",
        openai_api_key="o",
    )
    llm = build_llm(chain, client)
    assert isinstance(llm, FallbackLLM)
    assert (llm.provider, llm.model) == ("gemini", "gemini-3.1-flash-lite")


async def test_container_wires_configured_providers():
    settings = Settings(
        _env_file=None,
        search_provider="brave",
        serper_api_key="s",
        brave_api_key=None,
        youtube_api_key="y",
        llm_provider="anthropic",
        anthropic_api_key="a",
    )
    container = build_container(settings)
    try:
        assert container.search_provider == "serper"
        assert container.youtube_api is True
        assert container.llm is not None and container.llm.model == "claude-haiku-5-5"
    finally:
        await container.aclose()
