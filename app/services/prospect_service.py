from __future__ import annotations

import asyncio
import logging
from collections import Counter
from functools import partial

from app.config import Settings
from app.errors import DiscoveryUnavailableError, UpstreamError
from app.platforms.base import DiscoveryContext, PlatformAdapter
from app.platforms.registry import PlatformRegistry
from app.platforms.youtube import YouTubeAdapter
from app.schemas import (
    PLATFORM_ORDER,
    Platform,
    PlatformStatus,
    ProfileCandidate,
    Prospect,
    ProspectDiscoveryResponse,
    ProspectEntity,
    Relevance,
    SearchHit,
    SearchIntent,
    SocialLink,
)
from app.services.deduplication_service import DeduplicationService
from app.services.profile_extractor import CandidateFactory, WebsiteEnricher
from app.services.query_service import QueryService
from app.services.relevance_service import Assessment, Draft, RelevanceService, compute_signals
from app.services.search_service import SearchService
from app.utils.cache import AsyncTTLCache
from app.utils.normalizers import fold, name_tokens, normalize_handle
from app.utils.validators import whatsapp_link

logger = logging.getLogger(__name__)

_NAME_PRIORITY = {
    Platform.LINKEDIN: 0,
    Platform.GOOGLE: 1,
    Platform.FACEBOOK: 2,
    Platform.YOUTUBE: 3,
    Platform.X: 4,
    Platform.INSTAGRAM: 5,
    Platform.THREADS: 6,
}
_LOOKUP_PLATFORMS = (Platform.INSTAGRAM, Platform.X, Platform.THREADS, Platform.FACEBOOK)
_CONTACT_PLATFORMS = (Platform.EMAIL, Platform.WHATSAPP, Platform.PHONE)


def best_name(members: list[ProfileCandidate]) -> str | None:
    named = sorted(
        (m for m in members if m.name),
        key=lambda m: _NAME_PRIORITY.get(m.platform, 9),
    )
    full = [m.name for m in named if m.name and not m.name.startswith("@") and len(name_tokens(m.name)) >= 2]
    if full:
        return full[0]
    plain = [m.name for m in named if m.name and not m.name.startswith("@")]
    if plain:
        return plain[0]
    handles = [m.name for m in named if m.name]
    return handles[0] if handles else None


def social_links(members: list[ProfileCandidate]) -> list[SocialLink]:
    links: dict[tuple[Platform, str], SocialLink] = {}
    for member in members:
        links.setdefault((member.platform, member.url), SocialLink(platform=member.platform, link=member.url))
        for email in sorted(member.contacts.emails):
            links.setdefault(
                (Platform.EMAIL, email), SocialLink(platform=Platform.EMAIL, link=f"mailto:{email}")
            )
        for number in sorted(member.contacts.whatsapp):
            link = whatsapp_link(number)
            links.setdefault((Platform.WHATSAPP, link), SocialLink(platform=Platform.WHATSAPP, link=link))
        for number in sorted(member.contacts.phones):
            links.setdefault(
                (Platform.PHONE, number), SocialLink(platform=Platform.PHONE, link=f"tel:{number}")
            )
    return sorted(links.values(), key=lambda s: PLATFORM_ORDER[s.platform])


class ProspectService:
    """Runs the discovery pipeline: understand the query, search every platform in parallel,
    extract and enrich profiles, consolidate duplicates, then keep only evidenced matches."""

    def __init__(
        self,
        *,
        settings: Settings,
        query_service: QueryService,
        search_service: SearchService | None,
        registry: PlatformRegistry,
        enricher: WebsiteEnricher | None,
        relevance: RelevanceService,
        response_cache: AsyncTTLCache[ProspectDiscoveryResponse],
    ) -> None:
        self._settings = settings
        self._query = query_service
        self._search = search_service
        self._registry = registry
        self._factory = CandidateFactory(registry)
        self._enricher = enricher
        self._dedup = DeduplicationService()
        self._relevance = relevance
        self._cache = response_cache

    async def discover(self, query: str, limit: int | None = None) -> ProspectDiscoveryResponse:
        limit = min(limit or self._settings.max_prospects, self._settings.max_prospects)
        key = (fold(query), limit)
        cached = self._cache.get(key)
        if cached is not None:
            return cached.model_copy(update={"query": query})
        response = await self._discover(query, limit)
        if all(p.status != "failed" for p in response.platforms):
            self._cache.set(key, response)
        return response

    async def _discover(self, query: str, limit: int) -> ProspectDiscoveryResponse:
        intent = await self._query.understand(query)
        region = intent.location.country_code
        hits, statuses = await self._search_platforms(intent)
        candidates = self._factory.build(hits, region)
        if not candidates and not any(s.status == "ok" for s in statuses.values()):
            raise DiscoveryUnavailableError(
                "Every discovery source failed or is not configured.",
                platforms=sorted(statuses.values(), key=lambda s: PLATFORM_ORDER[s.platform]),
            )

        await self._enrich(candidates, intent)
        clusters = self._dedup.cluster(candidates)
        drafts = [Draft(id=f"p{i}", members=members) for i, members in enumerate(clusters, 1)]
        assessments = await self._relevance.assess(drafts, intent)
        accepted = sorted(
            (d for d in drafts if d.id in assessments and assessments[d.id].accepted),
            key=lambda d: assessments[d.id].score,
            reverse=True,
        )
        await self._cross_platform_lookup(
            accepted[: self._settings.max_cross_platform_lookups], drafts, intent
        )

        prospects = [p for d in accepted if (p := self._to_prospect(d, assessments[d.id], intent))]
        prospects = prospects[:limit]
        self._count_profiles(statuses, candidates)
        return ProspectDiscoveryResponse(
            query=query,
            total_prospects=len(prospects),
            prospects=prospects,
            intent=intent,
            platforms=sorted(statuses.values(), key=lambda s: PLATFORM_ORDER[s.platform]),
        )

    async def _search_platforms(
        self, intent: SearchIntent
    ) -> tuple[list[SearchHit], dict[Platform, PlatformStatus]]:
        region = intent.location.country_code
        ctx = DiscoveryContext(intent=intent, web_search=partial(self._web_search, region=region))
        runnable: list[PlatformAdapter] = []
        statuses: dict[Platform, PlatformStatus] = {}
        for adapter in self._registry.adapters:
            api_backed = isinstance(adapter, YouTubeAdapter) and adapter.uses_official_api
            if self._search is None and not api_backed:
                statuses[adapter.platform] = PlatformStatus(
                    platform=adapter.platform, status="skipped", detail="no search provider configured"
                )
            else:
                runnable.append(adapter)
        results = await asyncio.gather(*(self._run_adapter(a, ctx) for a in runnable))
        hits: list[SearchHit] = []
        for adapter, (adapter_hits, status) in zip(runnable, results, strict=True):
            statuses[adapter.platform] = status
            hits.extend(adapter_hits)
        any_ok = any(s.status == "ok" for s in statuses.values())
        statuses[Platform.PHONE] = PlatformStatus(
            platform=Platform.PHONE,
            status="ok" if any_ok else "skipped",
            detail="extracted from public pages and bios" if any_ok else "no discovery source ran",
        )
        return hits, statuses

    async def _run_adapter(
        self, adapter: PlatformAdapter, ctx: DiscoveryContext
    ) -> tuple[list[SearchHit], PlatformStatus]:
        try:
            async with asyncio.timeout(self._settings.search_timeout_seconds * 3):
                hits = await adapter.discover(ctx)
        except UpstreamError as exc:
            logger.warning("%s discovery failed: %s", adapter.platform.value, exc)
            return [], PlatformStatus(platform=adapter.platform, status="failed", detail=exc.safe_message)
        except TimeoutError:
            return [], PlatformStatus(platform=adapter.platform, status="failed", detail="timed out")
        except Exception:
            logger.exception("%s discovery raised an unexpected error", adapter.platform.value)
            return [], PlatformStatus(platform=adapter.platform, status="failed", detail="unexpected error")
        detail = None
        if isinstance(adapter, YouTubeAdapter):
            detail = "YouTube Data API v3" if adapter.uses_official_api else "web search fallback"
        return hits, PlatformStatus(platform=adapter.platform, status="ok", detail=detail)

    async def _web_search(self, query: str, source: Platform, *, region: str | None) -> list[SearchHit]:
        if self._search is None:
            return []
        return await self._search.search(query, source, region)

    async def _enrich(self, candidates: list[ProfileCandidate], intent: SearchIntent) -> None:
        if self._enricher is None or self._settings.max_websites_per_request == 0:
            return
        sites = [c for c in candidates if c.platform == Platform.GOOGLE]

        def priority(site: ProfileCandidate) -> int:
            signals = compute_signals(site.text, intent)
            return -(2 * bool(signals.interest_terms) + bool(signals.location_level))

        sites.sort(key=priority)
        await self._enricher.enrich(
            sites[: self._settings.max_websites_per_request], intent.location.country_code
        )
        known = {c.url for c in candidates}
        for site in sites:
            for url in sorted(site.linked_urls):
                if url in known:
                    continue
                linked = self._factory.from_link(url, len(candidates) + 1)
                if linked is not None:
                    candidates.append(linked)
                    known.add(url)

    async def _cross_platform_lookup(
        self, drafts: list[Draft], all_drafts: list[Draft], intent: SearchIntent
    ) -> None:
        """Search the platforms a top prospect is missing from for the same handle. A hit is
        attached only if it passes the same identity rule used for deduplication."""
        search = self._search
        if search is None or not self._settings.enable_cross_platform_lookup:
            return
        taken = {m.url for d in all_drafts for m in d.members}

        async def lookup(draft: Draft) -> None:
            present = {m.platform for m in draft.members}
            handles = [m.handle for m in draft.members if m.platform in _LOOKUP_PLATFORMS and m.handle]
            missing = [p for p in _LOOKUP_PLATFORMS if p not in present]
            if not handles or not missing or len(normalize_handle(handles[0])) < 5:
                return
            sites: list[str] = []
            for platform in missing:
                adapter = self._registry.get(platform)
                if adapter is not None:
                    sites.extend(f"site:{host}" for host in adapter.hosts)
            query = f'"{handles[0]}" ({" OR ".join(sites)})'
            try:
                async with asyncio.timeout(self._settings.search_timeout_seconds):
                    hits = await search.search(query, Platform.GOOGLE, intent.location.country_code)
            except (UpstreamError, TimeoutError) as exc:
                logger.info("Cross-platform lookup skipped: %s", type(exc).__name__)
                return
            for candidate in self._factory.build(hits, intent.location.country_code):
                if (
                    candidate.platform in missing
                    and candidate.url not in taken
                    and self._dedup.can_join(draft.members, candidate)
                ):
                    candidate.id = f"{draft.id}-{candidate.platform.value.lower()}"
                    candidate.sources.add("cross-platform lookup")
                    draft.members.append(candidate)
                    taken.add(candidate.url)

        await asyncio.gather(*(lookup(d) for d in drafts))

    def _to_prospect(self, draft: Draft, assessment: Assessment, intent: SearchIntent) -> Prospect | None:
        name = best_name(draft.members)
        if not name:
            return None
        location = intent.location.display(assessment.location_level) if assessment.location_level else None
        page_locations = [m.page_location for m in draft.members if m.page_location]
        if not location and page_locations:
            location = page_locations[0]
        entity: ProspectEntity = "individual"
        if assessment.entity_type in ("business", "organization"):
            entity = assessment.entity_type  # type: ignore[assignment]
        return Prospect(
            name=name,
            location=location,
            entity_type=entity,
            social_link=social_links(draft.members),
            relevance=Relevance(
                score=assessment.score,
                interest_match=assessment.interest_match,
                location_match=assessment.location_match,
                method=assessment.method,
                evidence=assessment.evidence,
            ),
        )

    @staticmethod
    def _count_profiles(statuses: dict[Platform, PlatformStatus], candidates: list[ProfileCandidate]) -> None:
        counts: Counter[Platform] = Counter(c.platform for c in candidates)
        for candidate in candidates:
            counts[Platform.EMAIL] += len(candidate.contacts.emails)
            counts[Platform.WHATSAPP] += len(candidate.contacts.whatsapp)
            counts[Platform.PHONE] += len(candidate.contacts.phones)
        for platform, status in statuses.items():
            if status.status == "ok":
                status.profiles_found = counts.get(platform, 0)
