from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.platforms.base import (
    ParsedProfile,
    PlatformAdapter,
    any_of,
    join_query,
    location_term,
    quote,
)
from app.schemas import Platform, SearchIntent
from app.utils.normalizers import clean_display_name, clean_text, registrable_domain

SOCIAL_EXCLUSIONS = (
    "-site:linkedin.com -site:facebook.com -site:instagram.com -site:x.com -site:twitter.com "
    "-site:youtube.com -site:threads.net -site:threads.com"
)

# Registrable domains whose pages describe or list other people (search engines, news,
# directories, review sites, marketplaces, multi-tenant hosts). They are never prospects.
NON_PROSPECT_DOMAINS = frozenset(
    {
        "google.com",
        "bing.com",
        "yahoo.com",
        "duckduckgo.com",
        "baidu.com",
        "yandex.com",
        "wikipedia.org",
        "wikimedia.org",
        "wikidata.org",
        "fandom.com",
        "britannica.com",
        "reddit.com",
        "quora.com",
        "pinterest.com",
        "tiktok.com",
        "snapchat.com",
        "tumblr.com",
        "medium.com",
        "substack.com",
        "github.com",
        "linktr.ee",
        "beacons.ai",
        "about.me",
        "yelp.com",
        "tripadvisor.com",
        "zomato.com",
        "foursquare.com",
        "opentable.com",
        "talabat.com",
        "deliveroo.co.uk",
        "deliveroo.ae",
        "ubereats.com",
        "doordash.com",
        "glassdoor.com",
        "indeed.com",
        "crunchbase.com",
        "zoominfo.com",
        "rocketreach.co",
        "apollo.io",
        "signalhire.com",
        "lusha.com",
        "clutch.co",
        "upcity.com",
        "goodfirms.co",
        "designrush.com",
        "sortlist.com",
        "themanifest.com",
        "expertise.com",
        "bbb.org",
        "yellowpages.com",
        "yell.com",
        "justdial.com",
        "sulekha.com",
        "indiamart.com",
        "eventbrite.com",
        "meetup.com",
        "amazon.com",
        "ebay.com",
        "etsy.com",
        "apple.com",
        "microsoft.com",
        "nytimes.com",
        "washingtonpost.com",
        "bbc.co.uk",
        "bbc.com",
        "cnn.com",
        "forbes.com",
        "bloomberg.com",
        "theguardian.com",
        "reuters.com",
        "indiatimes.com",
        "hindustantimes.com",
        "ndtv.com",
        "gulfnews.com",
        "khaleejtimes.com",
        "thenationalnews.com",
        "espn.com",
        "espncricinfo.com",
        "nfl.com",
        "fifa.com",
        "icc-cricket.com",
        "mlssoccer.com",
        "si.com",
        "bleacherreport.com",
        "theathletic.com",
        "cricbuzz.com",
        "msn.com",
        "imdb.com",
        "spotify.com",
        "soundcloud.com",
        "twitch.tv",
        "discord.com",
        "discord.gg",
        "telegram.org",
        "t.me",
        "whatsapp.com",
        "wa.me",
        "techcrunch.com",
        "businessinsider.com",
        "inc.com",
        "entrepreneur.com",
        "timeout.com",
        "eater.com",
        "zillow.com",
        "craigslist.org",
        "trustpilot.com",
        "g2.com",
        "capterra.com",
        "f6s.com",
        "angel.co",
        "wellfound.com",
        "producthunt.com",
        "ycombinator.com",
        "seedtable.com",
        "tracxn.com",
        "pitchbook.com",
        "dnb.com",
        "companieshouse.gov.uk",
        "opencorporates.com",
        "mapquest.com",
        "waze.com",
        "slideshare.net",
        "scribd.com",
        "issuu.com",
        "prnewswire.com",
        "businesswire.com",
        "globenewswire.com",
    }
)
_NON_PROSPECT_SUFFIXES = (".gov", ".mil", ".edu", ".gov.uk", ".gov.in", ".gov.ae", ".ac.uk")
_LISTICLE = re.compile(
    r"\b(?:top|best)\s+\d+\b|\b\d+\s+(?:best|top|great|leading)\b|\blist of\b|\bnear me\b"
    r"|\bdirectory\b|\branking(?:s)?\b",
    re.IGNORECASE,
)
_TITLE_SPLIT = re.compile(r"\s+[|•·:–—-]\s+")


def is_listicle_title(title: str) -> bool:
    return bool(_LISTICLE.search(title))


class GoogleAdapter(PlatformAdapter):
    """Open-web discovery. A hit on any non-social site stands for the site owner, so the
    profile link is the site's home page rather than the search-result page."""

    platform = Platform.GOOGLE

    def owns(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        domain = registrable_domain(parts.hostname.lower())
        if domain in NON_PROSPECT_DOMAINS:
            return False
        return not ("." + domain).endswith(_NON_PROSPECT_SUFFIXES)

    def parse_url(self, url: str) -> ParsedProfile | None:
        if not self.owns(url):
            return None
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return ParsedProfile(self.platform, f"{parts.scheme}://{host}/", None)

    def extract_name(self, title: str, profile: ParsedProfile) -> str | None:
        segments = [clean_display_name(s) for s in _TITLE_SPLIT.split(clean_text(title))]
        names = [s for s in segments if s and len(s) >= 2]
        return min(names, key=len) if names else None

    def build_queries(self, intent: SearchIntent) -> list[str]:
        where = location_term(intent)
        roles = any_of(intent.role_terms[:3])
        keywords = any_of(intent.keywords[:2]) or quote(intent.interest)
        if intent.target_type == "business":
            return [
                join_query(quote(intent.interest), where, SOCIAL_EXCLUSIONS),
                join_query(quote(intent.interest), where, '"contact us"', SOCIAL_EXCLUSIONS),
            ]
        return [join_query(keywords, roles, where, SOCIAL_EXCLUSIONS)]
