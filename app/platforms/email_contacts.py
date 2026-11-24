from __future__ import annotations

from app.platforms.base import (
    ParsedProfile,
    PlatformAdapter,
    any_of,
    join_query,
    location_term,
    quote,
)
from app.schemas import Platform, SearchIntent


class EmailAdapter(PlatformAdapter):
    """Looks for pages and bios that publish an email address. Addresses are only ever
    extracted from text a page shows; they are never constructed from names or domains."""

    platform = Platform.EMAIL

    def parse_url(self, url: str) -> ParsedProfile | None:
        return None

    def build_queries(self, intent: SearchIntent) -> list[str]:
        roles = any_of(intent.role_terms[:2])
        return [join_query(roles, quote(intent.interest), location_term(intent), '("email" OR "e-mail")')]
