from __future__ import annotations

import logging
import re
from typing import ClassVar

import httpx
import pycountry

from app.platforms.base import (
    DiscoveryContext,
    ParsedProfile,
    PlatformAdapter,
    any_of,
    join_query,
    location_term,
    path_segments,
    quote,
)
from app.schemas import Platform, SearchHit, SearchIntent
from app.utils.http import send_with_retry
from app.utils.normalizers import clean_text

logger = logging.getLogger(__name__)

_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE = re.compile(r"^@[A-Za-z0-9._-]{3,30}$")
_LEGACY = re.compile(r"^[A-Za-z0-9._-]{2,100}$")


class YouTubeDataClient:
    """Minimal client for the YouTube Data API v3 (search.list costs 100 quota units,
    channels.list costs 1)."""

    base_url = "https://www.googleapis.com/youtube/v3"

    def __init__(self, api_key: str, client: httpx.AsyncClient, timeout: float) -> None:
        self._headers = {"x-goog-api-key": api_key}
        self._client = client
        self._timeout = timeout

    async def search_channels(self, query: str, region_code: str | None, max_results: int) -> list[SearchHit]:
        params: dict[str, str | int] = {
            "part": "snippet",
            "type": "channel",
            "q": query,
            "maxResults": max_results,
        }
        if region_code:
            params["regionCode"] = region_code.upper()
        search = await send_with_retry(
            self._client,
            "GET",
            f"{self.base_url}/search",
            service="youtube",
            params=params,
            headers=self._headers,
            timeout=self._timeout,
        )
        ids = [
            item["id"]["channelId"]
            for item in search.json().get("items", [])
            if item.get("id", {}).get("channelId")
        ]
        if not ids:
            return []
        details = await send_with_retry(
            self._client,
            "GET",
            f"{self.base_url}/channels",
            service="youtube",
            params={"part": "snippet", "id": ",".join(ids), "maxResults": len(ids)},
            headers=self._headers,
            timeout=self._timeout,
        )
        by_id = {item["id"]: item for item in details.json().get("items", [])}
        hits: list[SearchHit] = []
        for position, channel_id in enumerate(ids, start=1):
            item = by_id.get(channel_id)
            if item is None or not _CHANNEL_ID.match(channel_id):
                continue
            snippet = item.get("snippet", {})
            custom = str(snippet.get("customUrl") or "")
            url = (
                f"https://www.youtube.com/{custom}"
                if _HANDLE.match(custom)
                else f"https://www.youtube.com/channel/{channel_id}"
            )
            description = clean_text(snippet.get("description"))[:700]
            country = _country_name(snippet.get("country"))
            text = f"{description} Channel country: {country}." if country else description
            hits.append(
                SearchHit(
                    url=url,
                    title=f"{clean_text(snippet.get('title'))} - YouTube",
                    snippet=text,
                    query=query,
                    source=Platform.YOUTUBE,
                    position=position,
                )
            )
        return hits


def _country_name(code: object) -> str | None:
    if not isinstance(code, str) or len(code) != 2:
        return None
    country = pycountry.countries.get(alpha_2=code.upper())
    return str(country.name) if country else None


class YouTubeAdapter(PlatformAdapter):
    platform = Platform.YOUTUBE
    hosts = ("youtube.com",)
    title_suffixes = (" - YouTube",)
    _reserved: ClassVar[frozenset[str]] = frozenset(
        {"watch", "shorts", "results", "playlist", "feed", "embed", "live", "hashtag"}
    )

    def __init__(self, api: YouTubeDataClient | None = None, max_channels: int = 15) -> None:
        self._api = api
        self._max_channels = max_channels

    @property
    def uses_official_api(self) -> bool:
        return self._api is not None

    def parse_url(self, url: str) -> ParsedProfile | None:
        if not self.owns(url):
            return None
        segments = path_segments(url)
        if not segments or segments[0].lower() in self._reserved:
            return None
        head = segments[0]
        if _HANDLE.match(head):
            handle = head[1:].lower()
            return ParsedProfile(self.platform, f"https://www.youtube.com/@{handle}", handle)
        if head == "channel" and len(segments) > 1 and _CHANNEL_ID.match(segments[1]):
            return ParsedProfile(self.platform, f"https://www.youtube.com/channel/{segments[1]}", None)
        if head in ("c", "user") and len(segments) > 1 and _LEGACY.match(segments[1]):
            return ParsedProfile(self.platform, f"https://www.youtube.com/{head}/{segments[1]}", segments[1])
        return None

    def build_queries(self, intent: SearchIntent) -> list[str]:
        keywords = any_of(intent.keywords[:2]) or quote(intent.interest)
        return [join_query("site:youtube.com", keywords, location_term(intent), '"subscribers"')]

    async def discover(self, ctx: DiscoveryContext) -> list[SearchHit]:
        if self._api is None:
            return await super().discover(ctx)
        intent = ctx.intent
        place = intent.location.city or intent.location.state or intent.location.country or ""
        query = " ".join(p for p in (intent.interest, place) if p)
        return await self._api.search_channels(query, intent.location.country_code, self._max_channels)
