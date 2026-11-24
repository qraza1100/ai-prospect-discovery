from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from app.config import Settings
from app.platforms.registry import PlatformRegistry
from app.platforms.youtube import YouTubeDataClient
from app.services.llm_service import FallbackLLM, LLMClient, build_llm
from app.services.profile_extractor import WebsiteEnricher
from app.services.prospect_service import ProspectService
from app.services.query_service import QueryService
from app.services.relevance_service import RelevanceService
from app.services.search_service import BraveBackend, SearchBackend, SearchService, SerperBackend
from app.utils.cache import AsyncTTLCache
from app.utils.http import RobotsPolicy, SafeFetcher
from app.utils.rate_limit import SlidingWindowRateLimiter

logger = logging.getLogger(__name__)


@dataclass
class Container:
    """Composition root: every long-lived object is created here and closed on shutdown."""

    settings: Settings
    prospect_service: ProspectService
    rate_limiter: SlidingWindowRateLimiter
    search_provider: str | None = None
    llm: LLMClient | None = None
    youtube_api: bool = False
    _closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    async def aclose(self) -> None:
        for close in reversed(self._closers):
            try:
                await close()
            except Exception:
                logger.exception("Error while closing a client")


def _select_backend(settings: Settings, client: httpx.AsyncClient) -> SearchBackend | None:
    keys = {"serper": settings.serper_api_key, "brave": settings.brave_api_key}
    order = [settings.search_provider, *(p for p in keys if p != settings.search_provider)]
    for provider in order:
        secret = keys[provider]
        if secret is None or not secret.get_secret_value():
            continue
        if provider != settings.search_provider:
            logger.warning("%s has no API key; using %s instead", settings.search_provider, provider)
        cls = SerperBackend if provider == "serper" else BraveBackend
        return cls(secret.get_secret_value(), client, settings.search_timeout_seconds)
    logger.warning("No search API key configured; only API-backed platforms will run")
    return None


def build_container(settings: Settings) -> Container:
    api_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.search_timeout_seconds, connect=5.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        headers={"User-Agent": settings.user_agent},
        follow_redirects=False,
    )
    web_client = SafeFetcher.build_client(settings.website_timeout_seconds)

    llm = build_llm(settings, api_client)
    backend = _select_backend(settings, api_client)
    search = (
        SearchService(
            backend,
            AsyncTTLCache(settings.cache_max_entries, settings.cache_ttl_seconds),
            results_per_query=settings.search_results_per_query,
            concurrency=settings.search_concurrency,
        )
        if backend
        else None
    )

    youtube = None
    if settings.youtube_api_key and settings.youtube_api_key.get_secret_value():
        youtube = YouTubeDataClient(
            settings.youtube_api_key.get_secret_value(), api_client, settings.search_timeout_seconds
        )
    registry = PlatformRegistry.default(youtube, settings.youtube_max_channels)

    enricher = None
    if settings.enable_website_enrichment:
        fetcher = SafeFetcher(
            web_client,
            user_agent=settings.user_agent,
            max_bytes=settings.website_max_bytes,
            timeout=settings.website_timeout_seconds,
        )
        robots = RobotsPolicy(fetcher, settings.user_agent, AsyncTTLCache(512, 3600))
        enricher = WebsiteEnricher(
            fetcher,
            robots,
            registry,
            AsyncTTLCache(settings.cache_max_entries, settings.cache_ttl_seconds),
            deadline=settings.enrichment_timeout_seconds,
        )

    service = ProspectService(
        settings=settings,
        query_service=QueryService(
            llm, AsyncTTLCache(settings.cache_max_entries, settings.intent_cache_ttl_seconds)
        ),
        search_service=search,
        registry=registry,
        enricher=enricher,
        relevance=RelevanceService(llm, settings.relevance_batch_size),
        response_cache=AsyncTTLCache(settings.cache_max_entries, settings.cache_ttl_seconds),
    )

    closers: list[Callable[[], Awaitable[None]]] = [api_client.aclose, web_client.aclose]
    if isinstance(llm, FallbackLLM):
        closers.append(llm.aclose)

    return Container(
        settings=settings,
        prospect_service=service,
        rate_limiter=SlidingWindowRateLimiter(settings.rate_limit_per_minute),
        search_provider=backend.name if backend else None,
        llm=llm,
        youtube_api=youtube is not None,
        _closers=closers,
    )
