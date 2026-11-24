from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Protocol

import anthropic
import httpx
from anthropic.types import MessageParam, OutputConfigParam

from app.config import Settings
from app.errors import LLMError, UpstreamError
from app.utils.http import send_with_retry

logger = logging.getLogger(__name__)

JSONSchema = dict[str, Any]


class LLMClient(Protocol):
    provider: str
    model: str

    async def generate_json(
        self, *, system: str, prompt: str, schema: JSONSchema, max_tokens: int
    ) -> dict[str, Any]: ...


def _parse_json(text: str, provider: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{provider} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise LLMError(f"{provider} returned JSON that is not an object")
    return value


class AnthropicLLM:
    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float,
        effort: str,
        http_client: anthropic.DefaultAsyncHttpxClient | None = None,
    ) -> None:
        self.model = model
        self._effort = effort
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout, max_retries=1, http_client=http_client
        )

    async def generate_json(
        self, *, system: str, prompt: str, schema: JSONSchema, max_tokens: int
    ) -> dict[str, Any]:
        output_config: OutputConfigParam = {"format": {"type": "json_schema", "schema": schema}}
        if self._effort:
            output_config["effort"] = self._effort  # type: ignore[typeddict-item]
        messages: list[MessageParam] = [{"role": "user", "content": prompt}]
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config=output_config,
            )
        except anthropic.RateLimitError as exc:
            raise LLMError("anthropic rate limit reached") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"anthropic returned HTTP {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("anthropic connection failed") from exc
        if response.stop_reason == "refusal":
            raise LLMError("anthropic declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("anthropic output was truncated")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return _parse_json(text, self.provider)

    async def aclose(self) -> None:
        await self._client.close()


class GeminiLLM:
    provider = "gemini"
    _url = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient, timeout: float) -> None:
        self.model = model
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    async def generate_json(
        self, *, system: str, prompt: str, schema: JSONSchema, max_tokens: int
    ) -> dict[str, Any]:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "maxOutputTokens": max_tokens,
            },
        }
        try:
            response = await send_with_retry(
                self._client,
                "POST",
                self._url.format(model=self.model),
                service="gemini",
                json=body,
                headers={"x-goog-api-key": self._api_key},
                timeout=self._timeout,
            )
        except UpstreamError as exc:
            raise LLMError(f"gemini {exc.safe_message}") from exc
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError("gemini returned no candidates")
        first = candidates[0]
        if first.get("finishReason") not in (None, "STOP"):
            raise LLMError(f"gemini stopped early: {first.get('finishReason')}")
        parts = first.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return _parse_json(text, self.provider)


class OpenAILLM:
    provider = "openai"
    _url = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient, timeout: float) -> None:
        self.model = model
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    async def generate_json(
        self, *, system: str, prompt: str, schema: JSONSchema, max_tokens: int
    ) -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": schema},
            },
            "max_completion_tokens": max_tokens,
        }
        try:
            response = await send_with_retry(
                self._client,
                "POST",
                self._url,
                service="openai",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        except UpstreamError as exc:
            raise LLMError(f"openai {exc.safe_message}") from exc
        choice = (response.json().get("choices") or [{}])[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            raise LLMError("openai declined the request")
        if choice.get("finish_reason") == "length":
            raise LLMError("openai output was truncated")
        return _parse_json(message.get("content") or "", self.provider)


class FallbackLLM:
    """Tries each client in order and returns the first valid answer."""

    def __init__(self, clients: list[LLMClient], attempt_timeout: float = 30.0) -> None:
        if not clients:
            raise ValueError("at least one LLM client is required")
        self._clients = clients
        self._attempt_timeout = attempt_timeout
        self.provider = clients[0].provider
        self.model = clients[0].model

    async def generate_json(
        self, *, system: str, prompt: str, schema: JSONSchema, max_tokens: int
    ) -> dict[str, Any]:
        last: LLMError | None = None
        for client in self._clients:
            started = time.perf_counter()
            try:
                async with asyncio.timeout(self._attempt_timeout):
                    result = await client.generate_json(
                        system=system, prompt=prompt, schema=schema, max_tokens=max_tokens
                    )
            except TimeoutError:
                logger.warning("LLM %s/%s timed out", client.provider, client.model)
                last = LLMError(f"{client.provider} timed out")
                continue
            except LLMError as exc:
                logger.warning("LLM %s/%s failed: %s", client.provider, client.model, exc)
                last = exc
                continue
            logger.info(
                "LLM %s/%s answered in %.0f ms",
                client.provider,
                client.model,
                (time.perf_counter() - started) * 1000,
            )
            return result
        raise last or LLMError("no LLM client succeeded")

    async def aclose(self) -> None:
        for client in self._clients:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


def build_llm_client(
    provider: str, model: str | None, settings: Settings, client: httpx.AsyncClient
) -> LLMClient | None:
    if provider == "none" or not model:
        return None
    keys = {
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.gemini_api_key,
        "openai": settings.openai_api_key,
    }
    secret = keys.get(provider)
    if secret is None or not secret.get_secret_value():
        logger.warning("LLM provider %s selected but its API key is not set", provider)
        return None
    key = secret.get_secret_value()
    if provider == "anthropic":
        return AnthropicLLM(key, model, settings.llm_timeout_seconds, settings.llm_effort)
    if provider == "gemini":
        return GeminiLLM(key, model, client, settings.llm_timeout_seconds)
    return OpenAILLM(key, model, client, settings.llm_timeout_seconds)


def build_llm(settings: Settings, client: httpx.AsyncClient) -> LLMClient | None:
    clients = [
        c
        for c in (
            build_llm_client(settings.llm_provider, settings.resolved_llm_model, settings, client),
            build_llm_client(
                settings.llm_fallback_provider, settings.resolved_fallback_model, settings, client
            ),
        )
        if c is not None
    ]
    return FallbackLLM(clients, settings.llm_timeout_seconds) if clients else None
