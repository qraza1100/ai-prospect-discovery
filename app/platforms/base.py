from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlsplit

from app.schemas import EntityKind, Platform, SearchHit, SearchIntent
from app.utils.normalizers import clean_display_name, clean_text, host_of

WebSearch = Callable[[str, Platform], Awaitable[list[SearchHit]]]

_AT_HANDLE = re.compile(r"\s*\(@[\w.]+\)")
_SEPARATORS = re.compile(r"\s+[|•·–—-]\s+")


@dataclass(frozen=True, slots=True)
class ParsedProfile:
    platform: Platform
    url: str
    handle: str | None
    kind: EntityKind = "unknown"


@dataclass(slots=True)
class DiscoveryContext:
    intent: SearchIntent
    web_search: WebSearch


def quote(term: str) -> str:
    return f'"{term}"' if term else ""


def any_of(terms: list[str]) -> str:
    quoted = [quote(t) for t in terms if t]
    if not quoted:
        return ""
    return quoted[0] if len(quoted) == 1 else "(" + " OR ".join(quoted) + ")"


def location_term(intent: SearchIntent) -> str:
    loc = intent.location
    return quote(loc.city or loc.state or loc.country or "")


def join_query(*parts: str) -> str:
    return " ".join(p for p in parts if p).strip()


def path_segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


class PlatformAdapter(ABC):
    """One integration per platform. Adapters build search queries for their platform and
    recognise which URLs are genuine profile pages (as opposed to posts, search pages or
    login walls)."""

    platform: ClassVar[Platform]
    hosts: ClassVar[tuple[str, ...]] = ()
    title_suffixes: ClassVar[tuple[str, ...]] = ()

    def owns(self, url: str) -> bool:
        host = host_of(url)
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    @abstractmethod
    def parse_url(self, url: str) -> ParsedProfile | None: ...

    @abstractmethod
    def build_queries(self, intent: SearchIntent) -> list[str]: ...

    async def discover(self, ctx: DiscoveryContext) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for query in self.build_queries(ctx.intent):
            hits.extend(await ctx.web_search(query, self.platform))
        return hits

    def extract_name(self, title: str, profile: ParsedProfile) -> str | None:
        text = clean_text(title)
        for suffix in self.title_suffixes:
            idx = text.lower().find(suffix.lower())
            if idx > 0:
                text = text[:idx]
        text = _AT_HANDLE.split(text, maxsplit=1)[0]
        text = re.split(r"\s+on\s+(?:X|Twitter|Instagram|Threads|Facebook)\b", text)[0]
        first = _SEPARATORS.split(text, maxsplit=1)[0]
        name = clean_display_name(first)
        if name and profile.handle and name.lstrip("@").lower() == profile.handle.lower():
            return f"@{profile.handle}"
        return name
