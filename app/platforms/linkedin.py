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
from app.schemas import EntityKind, Platform, SearchIntent

_SLUG = re.compile(r"^[A-Za-z0-9%_.-]{2,100}$")


class LinkedInAdapter(PlatformAdapter):
    platform = Platform.LINKEDIN
    hosts = ("linkedin.com",)
    title_suffixes = (" | LinkedIn", " - LinkedIn")

    def parse_url(self, url: str) -> ParsedProfile | None:
        if not self.owns(url):
            return None
        segments = path_segments(url)
        if len(segments) < 2 or segments[0] not in ("in", "company"):
            return None
        slug = segments[1].lower()
        if not _SLUG.match(slug):
            return None
        kind: EntityKind = "individual" if segments[0] == "in" else "business"
        return ParsedProfile(self.platform, f"https://www.linkedin.com/{segments[0]}/{slug}", slug, kind)

    def build_queries(self, intent: SearchIntent) -> list[str]:
        where = location_term(intent)
        keywords = any_of(intent.keywords[:2]) or quote(intent.interest)
        roles = any_of(intent.role_terms[:3])
        queries = [join_query("site:linkedin.com/in", keywords, where)]
        if intent.target_type == "business":
            queries.append(join_query("site:linkedin.com/company", quote(intent.interest), where))
        if roles:
            queries.append(join_query("site:linkedin.com/in", roles, quote(intent.interest), where))
        return list(dict.fromkeys(queries))[:2]
