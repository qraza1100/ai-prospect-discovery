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

_HANDLE = re.compile(r"^@((?!\.)(?!.*\.\.)[A-Za-z0-9._]{1,30}(?<!\.))$")


class ThreadsAdapter(PlatformAdapter):
    platform = Platform.THREADS
    hosts = ("threads.net", "threads.com")
    title_suffixes = (" • Threads", " | Threads", " on Threads")

    def parse_url(self, url: str) -> ParsedProfile | None:
        if not self.owns(url):
            return None
        segments = path_segments(url)
        if not segments:
            return None
        match = _HANDLE.match(segments[0])
        if not match:
            return None
        if len(segments) > 1 and segments[1] != "post":
            return None
        handle = match.group(1).lower()
        return ParsedProfile(self.platform, f"https://www.threads.com/@{handle}", handle)

    def build_queries(self, intent: SearchIntent) -> list[str]:
        keywords = any_of(intent.keywords[:2]) or quote(intent.interest)
        return [join_query("(site:threads.net OR site:threads.com)", keywords, location_term(intent))]
