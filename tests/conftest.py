"""Shared fakes. All names, handles, domains and numbers below are synthetic test data."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.container import Container
from app.platforms.registry import PlatformRegistry
from app.platforms.youtube import YouTubeDataClient
from app.schemas import Location, SearchIntent
from app.services.profile_extractor import WebsiteEnricher
from app.services.prospect_service import ProspectService
from app.services.query_service import QueryService
from app.services.relevance_service import RelevanceService
from app.services.search_service import SearchResult, SearchService
from app.utils.cache import AsyncTTLCache
from app.utils.http import RobotsPolicy, SafeFetcher
from app.utils.rate_limit import SlidingWindowRateLimiter

PUBLIC_IP = "93.184.216.34"


class FakeLLM:
    provider = "fake"
    model = "fake-1"

    def __init__(self, handler: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    async def generate_json(
        self, *, system: str, prompt: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        payload = json.loads(prompt)
        self.calls.append(payload)
        return self.handler(system, payload)


class FakeBackend:
    name = "fake"

    def __init__(self, routes: dict[str, list[SearchResult]] | None = None, fail_on: str | None = None):
        self.routes = routes or {}
        self.fail_on = fail_on
        self.queries: list[str] = []

    async def search(self, query: str, *, country_code: str | None, num: int) -> list[SearchResult]:
        from app.errors import UpstreamError

        self.queries.append(query)
        if self.fail_on and self.fail_on in query:
            raise UpstreamError("fake", "rate limited", 429)
        for needle, results in self.routes.items():
            if needle in query:
                return results
        return []


def result(url: str, title: str, snippet: str, position: int = 1) -> SearchResult:
    return SearchResult(url=url, title=title, snippet=snippet, position=position)


def football_intent() -> SearchIntent:
    return SearchIntent(
        interest="Football",
        audience="Football lovers",
        location=Location(
            city="Washington DC", state="District of Columbia", country="USA", country_code="us"
        ),
        target_type="individual",
        keywords=["Football", "NFL", "soccer"],
        role_terms=["fan", "lover"],
        location_aliases=["Washington DC", "Washington, D.C.", "DC", "DMV"],
        source="llm",
    )


def intent_llm_answer() -> dict[str, Any]:
    return {
        "is_valid": True,
        "interest": "Football",
        "audience": "Football lovers",
        "target_type": "individual",
        "city": "Washington DC",
        "state": "District of Columbia",
        "country": "USA",
        "country_code": "us",
        "keywords": ["NFL", "soccer"],
        "role_terms": ["fan", "lover"],
        "location_aliases": ["Washington, D.C.", "DC", "DMV"],
    }


def echo_relevance(payload: dict[str, Any]) -> dict[str, Any]:
    """Marks every item relevant and quotes words that really occur in its text."""
    rows = []
    for item in payload["items"]:
        text = item["text"]
        rows.append(
            {
                "id": item["id"],
                "entity_type": "individual",
                "relevant": "football" in text.lower(),
                "interest_quote": "football" if "football" in text.lower() else "",
                "location_quote": "Washington DC" if "Washington DC" in text else "",
                "confidence": 0.9,
            }
        )
    return {"results": rows}


def default_llm_handler(system: str, payload: dict[str, Any]) -> dict[str, Any]:
    if "request" in payload:
        return intent_llm_answer()
    return echo_relevance(payload)


async def public_resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "test",
        "llm_provider": "none",
        "serper_api_key": None,
        "brave_api_key": None,
        "youtube_api_key": None,
        "rate_limit_per_minute": 0,
        "max_cross_platform_lookups": 4,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def make_container(
    settings: Settings,
    backend: FakeBackend | None,
    llm: FakeLLM | None,
    web_handler: Callable[[httpx.Request], httpx.Response] | None = None,
    youtube_handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> Container:
    youtube = None
    if youtube_handler is not None:
        youtube = YouTubeDataClient(
            "test-key", httpx.AsyncClient(transport=httpx.MockTransport(youtube_handler)), 5
        )
    registry = PlatformRegistry.default(youtube)
    enricher = None
    if web_handler is not None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(web_handler))
        fetcher = SafeFetcher(
            client, user_agent="TestBot/1.0", max_bytes=200_000, timeout=5, resolver=public_resolver
        )
        enricher = WebsiteEnricher(
            fetcher,
            RobotsPolicy(fetcher, "TestBot/1.0", AsyncTTLCache(16, 60)),
            registry,
            AsyncTTLCache(16, 60),
        )
    search = (
        SearchService(backend, AsyncTTLCache(256, 60), results_per_query=10, concurrency=4)
        if backend
        else None
    )
    service = ProspectService(
        settings=settings,
        query_service=QueryService(llm, AsyncTTLCache(64, 60)),
        search_service=search,
        registry=registry,
        enricher=enricher,
        relevance=RelevanceService(llm, settings.relevance_batch_size),
        response_cache=AsyncTTLCache(64, 60),
    )
    return Container(
        settings=settings,
        prospect_service=service,
        rate_limiter=SlidingWindowRateLimiter(settings.rate_limit_per_minute),
        search_provider=backend.name if backend else None,
        llm=llm,
        youtube_api=youtube is not None,
    )


@pytest.fixture
def intent() -> SearchIntent:
    return football_intent()
