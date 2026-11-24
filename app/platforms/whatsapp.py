from __future__ import annotations

from app.platforms.base import ParsedProfile, PlatformAdapter, join_query, location_term, quote
from app.schemas import Platform, SearchIntent


class WhatsAppAdapter(PlatformAdapter):
    """WhatsApp has no public profile directory. Discovery looks for pages and bios that
    publish a click-to-chat link; the number is then attached to the page owner."""

    platform = Platform.WHATSAPP
    hosts = ("wa.me", "whatsapp.com")

    def parse_url(self, url: str) -> ParsedProfile | None:
        return None

    def build_queries(self, intent: SearchIntent) -> list[str]:
        return [join_query(quote(intent.interest), location_term(intent), '("wa.me" OR "whatsapp")')]
