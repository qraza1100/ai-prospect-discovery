from __future__ import annotations

from app.platforms.base import ParsedProfile, PlatformAdapter
from app.platforms.email_contacts import EmailAdapter
from app.platforms.facebook import FacebookAdapter
from app.platforms.google import GoogleAdapter
from app.platforms.instagram import InstagramAdapter
from app.platforms.linkedin import LinkedInAdapter
from app.platforms.threads import ThreadsAdapter
from app.platforms.whatsapp import WhatsAppAdapter
from app.platforms.x_platform import XAdapter
from app.platforms.youtube import YouTubeAdapter, YouTubeDataClient
from app.schemas import Platform


class PlatformRegistry:
    """Holds every adapter. URL routing checks the platform adapters first and falls back to
    the open-web adapter, so a new platform only needs a new adapter registered here."""

    def __init__(self, adapters: list[PlatformAdapter]) -> None:
        self.adapters = adapters
        self._website = next(a for a in adapters if isinstance(a, GoogleAdapter))
        self._specific = [a for a in adapters if not isinstance(a, GoogleAdapter)]

    @classmethod
    def default(
        cls, youtube_api: YouTubeDataClient | None = None, youtube_max_channels: int = 15
    ) -> PlatformRegistry:
        return cls(
            [
                GoogleAdapter(),
                LinkedInAdapter(),
                XAdapter(),
                YouTubeAdapter(youtube_api, youtube_max_channels),
                InstagramAdapter(),
                FacebookAdapter(),
                ThreadsAdapter(),
                WhatsAppAdapter(),
                EmailAdapter(),
            ]
        )

    def adapter_for(self, url: str) -> PlatformAdapter | None:
        for adapter in self._specific:
            if adapter.owns(url):
                return adapter
        return self._website if self._website.owns(url) else None

    def parse(self, url: str) -> tuple[PlatformAdapter, ParsedProfile] | None:
        adapter = self.adapter_for(url)
        if adapter is None:
            return None
        parsed = adapter.parse_url(url)
        return (adapter, parsed) if parsed else None

    def get(self, platform: Platform) -> PlatformAdapter | None:
        return next((a for a in self.adapters if a.platform == platform), None)
