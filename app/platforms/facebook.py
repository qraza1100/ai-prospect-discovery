from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

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

_USERNAME = re.compile(r"^[A-Za-z0-9.\-]{5,50}$")
_NUMERIC_ID = re.compile(r"^\d{5,20}$")
_RESERVED = {
    "groups",
    "events",
    "watch",
    "marketplace",
    "gaming",
    "login",
    "login.php",
    "help",
    "policies",
    "privacy",
    "share",
    "sharer",
    "sharer.php",
    "dialog",
    "plugins",
    "story.php",
    "permalink.php",
    "photo.php",
    "photo",
    "photos",
    "video.php",
    "videos",
    "reel",
    "reels",
    "hashtag",
    "search",
    "ads",
    "business",
    "l.php",
    "media",
    "notes",
    "settings",
    "messages",
    "friends",
    "stories",
    "live",
    "fundraisers",
    "public",
    "places",
    "directory",
    "home.php",
    "recover",
    "r.php",
    "legal",
}
_PROFILE_SUBPATHS = {"about", "posts", "photos", "videos", "reviews", "menu", "community"}


class FacebookAdapter(PlatformAdapter):
    platform = Platform.FACEBOOK
    hosts = ("facebook.com", "fb.com")
    title_suffixes = (" | Facebook", " - Facebook", " on Facebook")

    def parse_url(self, url: str) -> ParsedProfile | None:
        if not self.owns(url):
            return None
        segments = path_segments(url)
        if not segments:
            return None
        head = segments[0]
        if head == "profile.php":
            profile_id = (parse_qs(urlsplit(url).query).get("id") or [""])[0]
            if not _NUMERIC_ID.match(profile_id):
                return None
            return ParsedProfile(
                self.platform, f"https://www.facebook.com/profile.php?id={profile_id}", profile_id
            )
        if head in ("people", "pages") and len(segments) >= 3 and _NUMERIC_ID.match(segments[2]):
            canonical = f"https://www.facebook.com/{head}/{segments[1]}/{segments[2]}/"
            kind: EntityKind = "business" if head == "pages" else "unknown"
            return ParsedProfile(self.platform, canonical, segments[2], kind)
        username = head.lower()
        if username in _RESERVED or not _USERNAME.match(username):
            return None
        if len(segments) > 1 and segments[1] not in _PROFILE_SUBPATHS:
            return None
        return ParsedProfile(self.platform, f"https://www.facebook.com/{username}", username)

    def build_queries(self, intent: SearchIntent) -> list[str]:
        keywords = any_of(intent.keywords[:2]) or quote(intent.interest)
        return [join_query("site:facebook.com", keywords, location_term(intent))]
