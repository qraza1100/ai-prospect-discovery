"""Regression tests for issues found in code review."""

import asyncio

import httpx

from app.errors import LLMError
from app.platforms.registry import PlatformRegistry
from app.schemas import Platform, ProfileCandidate
from app.services.llm_service import FallbackLLM
from app.services.profile_extractor import WebsiteEnricher
from app.services.query_service import parse_query_with_rules
from app.services.relevance_service import Draft
from app.utils.cache import AsyncTTLCache
from app.utils.http import RobotsPolicy, SafeFetcher
from tests.conftest import (
    FakeBackend,
    FakeLLM,
    football_intent,
    make_container,
    make_settings,
    public_resolver,
    result,
)


def fetcher_for(handler, timeout=5.0):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SafeFetcher(
        client, user_agent="TestBot/1.0", max_bytes=100_000, timeout=timeout, resolver=public_resolver
    )


async def test_idn_hosts_use_ascii_host_header_and_sni():
    seen = {}

    def handler(request):
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        seen["encoding"] = request.headers["accept-encoding"]
        return httpx.Response(200, html="ok")

    await fetcher_for(handler).fetch("https://münchen-fußball.de/")
    assert seen["host"] == "xn--mnchen-fuball-jdb04b.de"
    assert seen["sni"] == "xn--mnchen-fuball-jdb04b.de"
    assert seen["encoding"] == "identity"


async def test_slow_drip_response_hits_the_fetch_deadline():
    async def trickle():
        for _ in range(100):
            await asyncio.sleep(0.05)
            yield b"a"

    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=trickle())

    result_ = await fetcher_for(handler, timeout=0.1).fetch("https://slow.kickoff-dc.org/")
    assert result_.status is None and result_.text is None


async def test_enrichment_deadline_keeps_finished_sites():
    async def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.headers["host"] == "slow.kickoff-dc.org":
            await asyncio.sleep(5)
        return httpx.Response(200, html='<meta property="og:site_name" content="Fast Club">')

    registry = PlatformRegistry.default()
    fetcher = fetcher_for(handler)
    enricher = WebsiteEnricher(
        fetcher,
        RobotsPolicy(fetcher, "TestBot/1.0", AsyncTTLCache(8, 60)),
        registry,
        AsyncTTLCache(8, 60),
        deadline=0.5,
    )
    fast = ProfileCandidate(
        id="1",
        platform=Platform.GOOGLE,
        url="https://fast.kickoff-dc.org/",
        handle=None,
        name="Home",
        kind="unknown",
    )
    slow = ProfileCandidate(
        id="2",
        platform=Platform.GOOGLE,
        url="https://slow.kickoff-dc.org/",
        handle=None,
        name="Slow",
        kind="unknown",
    )
    started = asyncio.get_running_loop().time()
    await enricher.enrich([fast, slow], "us")
    assert asyncio.get_running_loop().time() - started < 2
    assert fast.name == "Fast Club"
    assert slow.name == "Slow"


async def test_cross_platform_lookup_never_reuses_another_prospects_profile():
    backend = FakeBackend({'"janeroe"': [result("https://x.com/janeroe", "Jane Roe (@janeroe) / X", "x")]})
    settings = make_settings()
    service = make_container(settings, backend, None).prospect_service
    ig = ProfileCandidate(
        id="1",
        platform=Platform.INSTAGRAM,
        url="https://www.instagram.com/janeroe/",
        handle="janeroe",
        name="Jane Roe",
        kind="unknown",
    )
    x = ProfileCandidate(
        id="2",
        platform=Platform.X,
        url="https://x.com/janeroe",
        handle="janeroe",
        name="Jane Roe",
        kind="unknown",
    )
    first, second = Draft(id="p1", members=[ig]), Draft(id="p2", members=[x])
    await service._cross_platform_lookup([first], [first, second], football_intent())
    assert [m.platform for m in first.members] == [Platform.INSTAGRAM]


def test_rule_parser_strips_operators_from_location():
    intent = parse_query_with_rules('Football fans in Washington" site:evil.example OR "x')
    assert intent.location.city is not None
    assert '"' not in intent.location.city and "site:" not in intent.location.city


async def test_fallback_llm_times_out_slow_provider():
    class Slow:
        provider, model = "slow", "s"

        async def generate_json(self, **kwargs):
            await asyncio.sleep(5)
            return {}

    chain = FallbackLLM([Slow(), FakeLLM(lambda s, p: {"ok": True})], attempt_timeout=0.1)
    assert await chain.generate_json(system="s", prompt="{}", schema={}, max_tokens=5) == {"ok": True}

    only_slow = FallbackLLM([Slow()], attempt_timeout=0.1)
    try:
        await only_slow.generate_json(system="s", prompt="{}", schema={}, max_tokens=5)
    except LLMError:
        pass
    else:
        raise AssertionError("expected LLMError")
