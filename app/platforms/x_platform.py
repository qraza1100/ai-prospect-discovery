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

_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_RESERVED = {
    "home",
    "explore",
    "search",
    "i",
    "intent",
    "hashtag",
    "share",
    "login",
    "logout",
    "signup",
    "settings",
    "notifications",
    "messages",
    "tos",
    "privacy",
    "about",
    "compose",
    "jobs",
    "download",
    "account",
    "oauth",
    "lists",
    "topics",
    "communities",
    "status",
    "x",
    "twitter",
}
_PROFILE_SUBPATHS = {"status", "with_replies", "media", "likes", "highlights", "photo"}


class XAdapter(PlatformAdapter):
    platform = Platform.X
    hosts = ("x.com", "twitter.com")
    title_suffixes = (" / X", " / Twitter", " | X", " on X:", " on Twitter:")

    def parse_url(self, url: str) -> ParsedProfile | None:
        if not self.owns(url):
            return None
        segments = path_segments(url)
        if not segments:
            return None
        handle = segments[0].lower()
        if handle in _RESERVED or not _HANDLE.match(handle):
            return None
        if len(segments) > 1 and segments[1] not in _PROFILE_SUBPATHS:
            return None
        return ParsedProfile(self.platform, f"https://x.com/{handle}", handle)

    def build_queries(self, intent: SearchIntent) -> list[str]:
        keywords = any_of(intent.keywords[:2]) or quote(intent.interest)
        return [join_query("(site:x.com OR site:twitter.com)", keywords, location_term(intent))]
