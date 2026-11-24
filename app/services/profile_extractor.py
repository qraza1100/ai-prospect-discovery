from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from app.errors import UnsafeURLError
from app.platforms.google import is_listicle_title
from app.platforms.registry import PlatformRegistry
from app.schemas import ContactSet, Platform, ProfileCandidate, SearchHit
from app.utils.cache import AsyncTTLCache
from app.utils.http import RobotsPolicy, SafeFetcher
from app.utils.normalizers import clean_display_name, clean_text, host_of, registrable_domain
from app.utils.validators import (
    email_from_mailto,
    extract_emails,
    extract_phones,
    extract_whatsapp_numbers,
    normalize_email,
    normalize_phone,
    phone_from_tel,
    whatsapp_number_from_url,
)

logger = logging.getLogger(__name__)

MAX_PROFILES_PER_PLATFORM = 2
MAX_EMAILS = 5
MAX_PHONES = 3
_MAX_TEXT = 200_000


def contacts_from_text(text: str, region: str | None) -> ContactSet:
    return ContactSet(
        emails=extract_emails(text),
        phones=extract_phones(text, region, require_context=True),
        whatsapp=extract_whatsapp_numbers(text),
    )


def identity_key(platform: Platform, url: str) -> str:
    if platform == Platform.GOOGLE:
        return f"web:{host_of(url)}"
    return url.lower().rstrip("/")


class CandidateFactory:
    """Turns raw search hits into profile candidates: routes each URL to its platform,
    keeps only genuine profile pages and merges repeated hits on the same profile."""

    def __init__(self, registry: PlatformRegistry) -> None:
        self._registry = registry

    def build(self, hits: list[SearchHit], region: str | None) -> list[ProfileCandidate]:
        by_key: dict[str, ProfileCandidate] = {}
        for hit in hits:
            routed = self._registry.parse(hit.url)
            if routed is None:
                continue
            adapter, parsed = routed
            if parsed.platform == Platform.GOOGLE and is_listicle_title(hit.title):
                continue
            key = identity_key(parsed.platform, parsed.url)
            contacts = contacts_from_text(f"{hit.title} {hit.snippet}", region)
            existing = by_key.get(key)
            if existing is not None:
                existing.sources.add(hit.source.value)
                existing.contacts.update(contacts)
                if hit.snippet and hit.snippet not in existing.snippet:
                    existing.snippet = f"{existing.snippet} {hit.snippet}".strip()[:900]
                continue
            by_key[key] = ProfileCandidate(
                id=f"c{len(by_key) + 1}",
                platform=parsed.platform,
                url=parsed.url,
                handle=parsed.handle,
                name=adapter.extract_name(hit.title, parsed),
                kind=parsed.kind,
                title=hit.title,
                snippet=hit.snippet,
                source_url=hit.url,
                sources={hit.source.value},
                contacts=contacts,
            )
        return list(by_key.values())

    def from_link(self, url: str, next_id: int) -> ProfileCandidate | None:
        routed = self._registry.parse(url)
        if routed is None or routed[1].platform == Platform.GOOGLE:
            return None
        parsed = routed[1]
        return ProfileCandidate(
            id=f"c{next_id}",
            platform=parsed.platform,
            url=parsed.url,
            handle=parsed.handle,
            name=None,
            kind=parsed.kind,
            sources={"website"},
        )


@dataclass(slots=True)
class PageFacts:
    site_name: str | None = None
    description: str = ""
    location: str = ""
    contacts: ContactSet = field(default_factory=ContactSet)
    social_urls: set[str] = field(default_factory=set)
    contact_page: str | None = None


def _jsonld_nodes(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_nodes(item)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from _jsonld_nodes(value["@graph"])


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


_CREDIT_LINK = re.compile(
    r"\b(?:designed|built|developed|powered|made|hosted|website|site|theme)\s+by\b", re.IGNORECASE
)


def _link_context(anchor: Tag) -> str:
    """Text around a link, used to spot "website by ..." credits that point at a third party."""
    parts = [anchor.get_text(" ", strip=True)]
    node = anchor.previous_sibling
    for _ in range(2):
        if node is None:
            break
        parts.insert(0, node.get_text(" ", strip=True) if isinstance(node, Tag) else str(node).strip())
        node = node.previous_sibling
    return " ".join(p for p in parts if p)


_ENTITY_TYPES = ("organization", "business", "person", "restaurant", "service", "store", "club")


def parse_page(html: str, page_url: str, registry: PlatformRegistry, region: str | None) -> PageFacts:
    """Extract owner-published facts from a site's own page. Nothing here is inferred:
    every email, number and profile link comes from markup or text on the page."""
    soup = BeautifulSoup(html, "lxml")
    facts = PageFacts()
    site_domain = registrable_domain(page_url)
    structured_phones: set[str] = set()
    declared: dict[Platform, set[str]] = defaultdict(set)
    linked: dict[Platform, set[str]] = defaultdict(set)

    def add_social(url: str, into: dict[Platform, set[str]]) -> None:
        routed = registry.parse(url)
        if routed and routed[1].platform != Platform.GOOGLE:
            into[routed[1].platform].add(routed[1].url)

    meta_site = soup.find("meta", attrs={"property": "og:site_name"})
    if isinstance(meta_site, Tag):
        facts.site_name = clean_display_name(str(meta_site.get("content") or ""))
    for attr, value in (("name", "description"), ("property", "og:description")):
        meta = soup.find("meta", attrs={attr: value})
        if isinstance(meta, Tag) and meta.get("content"):
            facts.description = clean_text(str(meta.get("content")))[:400]
            break

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or ""
        if len(raw) > 100_000:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _jsonld_nodes(data):
            types = " ".join(str(t) for t in _as_list(node.get("@type"))).lower()
            if not any(t in types for t in _ENTITY_TYPES):
                continue
            if not facts.site_name and isinstance(node.get("name"), str):
                facts.site_name = clean_display_name(node["name"])
            for email in _as_list(node.get("email")):
                if isinstance(email, str) and (address := normalize_email(email.removeprefix("mailto:"))):
                    facts.contacts.emails.add(address)
            for phone in _as_list(node.get("telephone")):
                if isinstance(phone, str) and (number := normalize_phone(phone, region)):
                    structured_phones.add(number)
            for link in _as_list(node.get("sameAs")):
                if isinstance(link, str):
                    add_social(link, declared)
            for address in _as_list(node.get("address")):
                if isinstance(address, dict) and not facts.location:
                    parts = [address.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
                    facts.location = ", ".join(
                        clean_text(p) for p in parts if isinstance(p, str) and p.strip()
                    )

    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()

    anchor_phones: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        lowered = href.lower()
        if lowered.startswith("mailto:"):
            if email := email_from_mailto(href):
                facts.contacts.emails.add(email)
            continue
        if lowered.startswith("tel:"):
            if phone := phone_from_tel(href, region):
                anchor_phones.add(phone)
            continue
        absolute = urljoin(page_url, href)
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        if number := whatsapp_number_from_url(absolute):
            facts.contacts.whatsapp.add(number)
            continue
        if not _CREDIT_LINK.search(_link_context(anchor)):
            add_social(absolute, linked)
        if (
            facts.contact_page is None
            and registrable_domain(absolute) == site_domain
            and "contact" in (lowered + " " + anchor.get_text(" ").lower())
        ):
            facts.contact_page = absolute.split("#", 1)[0]

    text = soup.get_text(" ", strip=True)[:_MAX_TEXT]
    facts.contacts.emails |= extract_emails(text)
    facts.contacts.whatsapp |= extract_whatsapp_numbers(text)
    text_phones = extract_phones(text, region, require_context=True)

    if len(facts.contacts.emails) > MAX_EMAILS:
        own = {e for e in facts.contacts.emails if e.endswith("@" + site_domain)}
        facts.contacts.emails = own if len(own) <= MAX_EMAILS else set()
    trusted_phones = structured_phones | anchor_phones
    phones = trusted_phones | text_phones
    if len(phones) > MAX_PHONES:
        phones = trusted_phones if len(trusted_phones) <= MAX_PHONES else set()
    facts.contacts.phones = phones
    if len(facts.contacts.whatsapp) > MAX_PHONES:
        facts.contacts.whatsapp = set()

    for platform in set(declared) | set(linked):
        urls = declared.get(platform) or linked.get(platform, set())
        if len(urls) <= MAX_PROFILES_PER_PLATFORM:
            facts.social_urls |= urls
    return facts


class WebsiteEnricher:
    """Reads the home page (and, when it has no email, the contact page) of discovered
    websites to collect owner-published contact details and social links."""

    def __init__(
        self,
        fetcher: SafeFetcher,
        robots: RobotsPolicy,
        registry: PlatformRegistry,
        cache: AsyncTTLCache[PageFacts | None],
        *,
        concurrency: int = 4,
        deadline: float = 15.0,
    ) -> None:
        self._fetcher = fetcher
        self._robots = robots
        self._registry = registry
        self._cache = cache
        self._semaphore = asyncio.Semaphore(concurrency)
        self._deadline = deadline

    async def enrich(self, sites: list[ProfileCandidate], region: str | None) -> None:
        """Enrich sites until the deadline. Sites finished by then keep their facts; the rest
        are left as found, and their loads keep running to warm the cache."""

        async def one(site: ProfileCandidate) -> None:
            try:
                facts = await self._facts(site.url, region)
            except Exception as exc:
                logger.info("Skipping %s: %s", host_of(site.url), type(exc).__name__)
                return
            if facts is None:
                return
            site.name = facts.site_name or site.name
            site.page_text = facts.description
            site.page_location = facts.location
            site.contacts.update(facts.contacts)
            site.linked_urls |= facts.social_urls

        try:
            async with asyncio.timeout(self._deadline):
                await asyncio.gather(*(one(site) for site in sites))
        except TimeoutError:
            logger.info("Website enrichment stopped at the %.0f s deadline", self._deadline)

    async def _facts(self, url: str, region: str | None) -> PageFacts | None:
        return await self._cache.get_or_create(url, lambda: self._load(url, region))

    async def _load(self, url: str, region: str | None) -> PageFacts | None:
        async with self._semaphore:
            facts = await self._read(url, region)
            if facts and not facts.contacts.emails and facts.contact_page:
                extra = await self._read(facts.contact_page, region)
                if extra:
                    facts.contacts.update(extra.contacts)
                    facts.location = facts.location or extra.location
            return facts

    async def _read(self, url: str, region: str | None) -> PageFacts | None:
        try:
            if not await self._robots.allowed(url):
                return None
            result = await self._fetcher.fetch(url)
        except UnsafeURLError as exc:
            logger.info("Refused to fetch %s: %s", host_of(url), exc)
            return None
        if result.text is None:
            return None
        return parse_page(result.text, result.url, self._registry, region)
