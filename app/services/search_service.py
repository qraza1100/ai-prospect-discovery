from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.schemas import Platform, SearchHit
from app.utils.cache import AsyncTTLCache
from app.utils.http import send_with_retry
from app.utils.normalizers import clean_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    position: int


class SearchBackend(Protocol):
    name: str

    async def search(self, query: str, *, country_code: str | None, num: int) -> list[SearchResult]: ...


class SerperBackend:
    """Google results through serper.dev (Google's own Custom Search JSON API is closed to
    new customers and shuts down on 1 January 2027)."""

    name = "serper"
    url = "https://google.serper.dev/search"

    def __init__(self, api_key: str, client: httpx.AsyncClient, timeout: float) -> None:
        self._headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
        self._client = client
        self._timeout = timeout

    async def search(self, query: str, *, country_code: str | None, num: int) -> list[SearchResult]:
        body: dict[str, str | int] = {"q": query, "num": num, "hl": "en"}
        if country_code:
            body["gl"] = country_code.lower()
        response = await send_with_retry(
            self._client,
            "POST",
            self.url,
            service=self.name,
            json=body,
            headers=self._headers,
            timeout=self._timeout,
        )
        return [
            SearchResult(
                url=item["link"],
                title=clean_text(item.get("title")),
                snippet=clean_text(item.get("snippet")),
                position=int(item.get("position") or i),
            )
            for i, item in enumerate(response.json().get("organic", []), start=1)
            if isinstance(item.get("link"), str)
        ]


class BraveBackend:
    name = "brave"
    url = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, client: httpx.AsyncClient, timeout: float) -> None:
        self._headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
        self._client = client
        self._timeout = timeout

    async def search(self, query: str, *, country_code: str | None, num: int) -> list[SearchResult]:
        params: dict[str, str | int] = {"q": query, "count": min(num, 20), "safesearch": "moderate"}
        if country_code:
            params["country"] = country_code.upper()
        response = await send_with_retry(
            self._client,
            "GET",
            self.url,
            service=self.name,
            params=params,
            headers=self._headers,
            timeout=self._timeout,
        )
        results = response.json().get("web", {}).get("results", [])
        return [
            SearchResult(
                url=item["url"],
                title=clean_text(item.get("title")),
                snippet=clean_text(
                    " ".join([item.get("description") or "", *(item.get("extra_snippets") or [])])
                ),
                position=i,
            )
            for i, item in enumerate(results, start=1)
            if isinstance(item.get("url"), str)
        ]


class SearchService:
    """Caches results per (query, country) and caps concurrent upstream calls."""

    def __init__(
        self,
        backend: SearchBackend,
        cache: AsyncTTLCache[list[SearchResult]],
        *,
        results_per_query: int,
        concurrency: int,
    ) -> None:
        self.backend = backend
        self._cache = cache
        self._num = results_per_query
        self._semaphore = asyncio.Semaphore(concurrency)

    async def search(self, query: str, source: Platform, country_code: str | None) -> list[SearchHit]:
        key = (self.backend.name, query, country_code or "")
        results = await self._cache.get_or_create(key, lambda: self._run(query, country_code))
        return [
            SearchHit(
                url=r.url,
                title=r.title,
                snippet=r.snippet,
                query=query,
                source=source,
                position=r.position,
            )
            for r in results
        ]

    async def _run(self, query: str, country_code: str | None) -> list[SearchResult]:
        async with self._semaphore:
            logger.debug("search %s: %s", self.backend.name, query)
            return await self.backend.search(query, country_code=country_code, num=self._num)
