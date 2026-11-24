from __future__ import annotations

import re

from app.platforms.base import (
    ParsedProfile,
    PlatformAdapter,
    any_of,
    join_query,
    location_term,
    path_segments,
    quote,
)
from app.schemas import Platform, SearchIntent

_HANDLE = re.compile(r"^(?!\.)(?!.*\.\.)[A-Za-z0-9._]{1,30}(?<!\.)$")
_RESERVED = {
    "p",
    "reel",
    "reels",
    "tv",
    "explore",
    "stories",
    "accounts",
    "direct",
    "about",
    "developer",
    "legal",
    "web",
    "static",
    "challenge",
    "privacy",
    "emails",
    "session",
    "api",
    "graphql",
    "popular",
    "locations",
    "tags",
    "s",
}
_POST_PATHS = {"p", "reel", "reels", "tv"}


class InstagramAdapter(PlatformAdapter):
    platform = Platform.INSTAGRAM
    hosts = ("instagram.com",)
    title_suffixes = (" • Instagram", " | Instagram", " - Instagram", " on Instagram")

    def parse_url(self, url: str) -> ParsedProfile | None:
        if not self.owns(url):
            return None
        segments = path_segments(url)
        if not segments:
            return None
        handle = segments[0].lower()
        if handle in _RESERVED or not _HANDLE.match(handle):
            return None
        if len(segments) > 1 and segments[1] not in _POST_PATHS:
            return None
        return ParsedProfile(self.platform, f"https://www.instagram.com/{handle}/", handle)

    def build_queries(self, intent: SearchIntent) -> list[str]:
        where = location_term(intent)
        keywords = any_of(intent.keywords[:2]) or quote(intent.interest)
        queries = [join_query("site:instagram.com", keywords, where)]
        roles = any_of(intent.role_terms[:3])
        if roles:
            queries.append(join_query("site:instagram.com", roles, quote(intent.interest), where))
        return list(dict.fromkeys(queries))
