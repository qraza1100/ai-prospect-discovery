"""End-to-end tests through the HTTP API with fake search, LLM and web backends."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import ProspectDiscoveryResponse
from tests.conftest import (
    PUBLIC_IP,
    FakeBackend,
    FakeLLM,
    default_llm_handler,
    make_container,
    make_settings,
    result,
)

QUERY = "Football lovers in Washington DC in USA"

ROUTES = {
    "-site:linkedin.com": [
        result(
            "https://www.kickoff-dc.org/events",
            "Events - Kickoff DC Supporters Club",
            "Washington DC football supporters club",
        ),
        result("https://www.footynews-demo.org/top-10", "Top 10 football bars in Washington DC", "listicle"),
    ],
    "site:instagram.com": [
        result(
            "https://www.instagram.com/janeroe.dc/",
            "Jane Roe (@janeroe.dc) • Instagram photos and videos",
            "Football fan ⚽ | Washington DC | email: jane.roe.dc@mailbox-demo.org",
        ),
        result(
            "https://www.instagram.com/dckickofffans/",
            "Kickoff DC (@dckickofffans) • Instagram photos and videos",
            "Washington DC football supporters. Match-day meetups.",
        ),
        result(
            "https://www.instagram.com/chicagofan/",
            "Sam Lee (@chicagofan) • Instagram",
            "Football fan from Chicago",
        ),
    ],
    "site:x.com": [
        result(
            "https://x.com/janeroe_dc",
            "Jane Roe (@janeroe_dc) / X",
            "Football lover. Washington DC. Season tickets every year.",
        ),
        result("https://x.com/search?q=football", "Search", "not a profile"),
    ],
    "site:linkedin.com/in": [
        result(
            "https://www.linkedin.com/in/john-smith-11",
            "John Smith - Accountant | LinkedIn",
            "Washington DC accountant. Gardening on weekends.",
        ),
    ],
    "site:threads": [
        result(
            "https://www.threads.net/@janeroe.dc",
            "Jane Roe (@janeroe.dc) • Threads, Say more",
            "Football Sundays",
        ),
    ],
}

SITE_HTML = """<html><head><meta property="og:site_name" content="Kickoff DC Supporters Club">
<meta name="description" content="Washington DC football supporters club."></head>
<body><a href="https://www.instagram.com/dckickofffans/">Instagram</a>
<a href="https://www.facebook.com/dckickoffclub">Facebook</a>
<a href="mailto:hello@kickoff-dc.org">Email</a>
<p>Call us: (202) 456-1111</p>
<a href="https://wa.me/12024561111">WhatsApp</a></body></html>"""


def web_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.host == PUBLIC_IP
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    if request.headers["host"] == "www.kickoff-dc.org" and request.url.path == "/":
        return httpx.Response(200, html=SITE_HTML)
    return httpx.Response(404)


def client_for(container, settings):
    return TestClient(create_app(settings, container))


@pytest.fixture
def settings():
    return make_settings()


def test_end_to_end_discovery_returns_consolidated_evidenced_prospects(settings):
    llm = FakeLLM(default_llm_handler)
    container = make_container(settings, FakeBackend(ROUTES), llm, web_handler=web_handler)
    with client_for(container, settings) as client:
        response = client.post("/prospect-discovery", json={"query": QUERY})

    assert response.status_code == 200
    body = response.json()
    ProspectDiscoveryResponse.model_validate(body)
    assert body["success"] is True
    assert body["query"] == QUERY
    assert body["intent"]["location"]["state"] == "District of Columbia"
    assert body["total_prospects"] == len(body["prospects"])

    by_name = {p["name"]: p for p in body["prospects"]}
    assert set(by_name) == {"Jane Roe", "Kickoff DC Supporters Club"}

    jane = {(s["platform"], s["link"]) for s in by_name["Jane Roe"]["social_link"]}
    assert ("Instagram", "https://www.instagram.com/janeroe.dc/") in jane
    assert ("Threads", "https://www.threads.com/@janeroe.dc") in jane
    assert ("Email", "mailto:jane.roe.dc@mailbox-demo.org") in jane
    assert by_name["Jane Roe"]["location"] == "Washington DC, USA"

    club = {(s["platform"], s["link"]) for s in by_name["Kickoff DC Supporters Club"]["social_link"]}
    assert ("Google", "https://www.kickoff-dc.org/") in club
    assert ("Instagram", "https://www.instagram.com/dckickofffans/") in club
    assert ("Facebook", "https://www.facebook.com/dckickoffclub") in club
    assert ("Email", "mailto:hello@kickoff-dc.org") in club
    assert ("WhatsApp", "https://wa.me/12024561111") in club
    assert ("Phone", "tel:+12024561111") in club

    for prospect in body["prospects"]:
        assert prospect["social_link"], "social_link must never be empty"
        links = [s["link"] for s in prospect["social_link"]]
        assert len(links) == len(set(links))
        assert all("search?" not in link for link in links)
        assert prospect["relevance"]["interest_match"] and prospect["relevance"]["location_match"]

    statuses = {p["platform"]: p["status"] for p in body["platforms"]}
    assert statuses["Instagram"] == "ok" and statuses["Google"] == "ok"


def test_matching_handle_and_name_consolidates_x_profile(settings):
    container = make_container(settings, FakeBackend(ROUTES), FakeLLM(default_llm_handler))
    with client_for(container, settings) as client:
        body = client.post("/prospect-discovery", json={"query": QUERY}).json()
    jane_links = [p for p in body["prospects"] if p["name"] == "Jane Roe"]
    assert len(jane_links) == 1
    platforms = [s["platform"] for s in jane_links[0]["social_link"]]
    assert "X" in platforms


def test_platform_failure_is_isolated(settings):
    backend = FakeBackend(ROUTES, fail_on="site:linkedin.com/in")
    container = make_container(settings, backend, FakeLLM(default_llm_handler))
    with client_for(container, settings) as client:
        response = client.post("/prospect-discovery", json={"query": QUERY})
    assert response.status_code == 200
    statuses = {p["platform"]: p for p in response.json()["platforms"]}
    assert statuses["LinkedIn"]["status"] == "failed"
    assert statuses["LinkedIn"]["detail"] == "rate limited"
    assert statuses["Instagram"]["status"] == "ok"
    assert response.json()["total_prospects"] >= 1


def test_works_without_llm_using_rules(settings):
    container = make_container(settings, FakeBackend(ROUTES), None)
    with client_for(container, settings) as client:
        body = client.post("/prospect-discovery", json={"query": QUERY}).json()
    assert body["intent"]["source"] == "rules"
    assert body["prospects"]
    assert all(p["relevance"]["method"] == "rules" for p in body["prospects"])


def test_no_sources_configured_returns_503(settings):
    container = make_container(settings, None, None)
    with client_for(container, settings) as client:
        response = client.post("/prospect-discovery", json={"query": QUERY})
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "discovery_unavailable"
    assert all(p["status"] == "skipped" for p in body["platforms"])


def test_youtube_uses_official_api_when_configured(settings):
    def youtube(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "test-key"
        assert "key" not in request.url.params
        if request.url.path.endswith("/search"):
            assert request.url.params["regionCode"] == "US"
            return httpx.Response(200, json={"items": [{"id": {"channelId": "UC" + "a" * 22}}]})
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "UC" + "a" * 22,
                        "snippet": {
                            "title": "DMV Football Talk",
                            "customUrl": "@dmvfootballtalk",
                            "description": "Football talk from Washington DC.",
                            "country": "US",
                        },
                    }
                ]
            },
        )

    container = make_container(settings, None, FakeLLM(default_llm_handler), youtube_handler=youtube)
    with client_for(container, settings) as client:
        body = client.post("/prospect-discovery", json={"query": QUERY}).json()
    statuses = {p["platform"]: p for p in body["platforms"]}
    assert statuses["YouTube"]["status"] == "ok"
    assert statuses["YouTube"]["detail"] == "YouTube Data API v3"
    assert statuses["LinkedIn"]["status"] == "skipped"
    [channel] = body["prospects"]
    assert channel["social_link"] == [
        {"platform": "YouTube", "link": "https://www.youtube.com/@dmvfootballtalk"}
    ]


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({}, "query"),
        ({"query": ""}, "query"),
        ({"query": "ab"}, "query"),
        ({"query": "x" * 301}, "query"),
        ({"query": "12345 !!!"}, "three letters"),
        ({"query": QUERY, "unexpected": True}, "unexpected"),
        ({"query": QUERY, "max_results": 0}, "max_results"),
    ],
)
def test_invalid_payloads_return_structured_422(settings, payload, fragment):
    container = make_container(settings, FakeBackend(ROUTES), None)
    with client_for(container, settings) as client:
        response = client.post("/prospect-discovery", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "invalid_request"
    assert fragment in body["error"]["message"]


def test_non_prospect_query_returns_400(settings):
    def handler(system, payload):
        if "request" in payload:
            return {
                "is_valid": False,
                "interest": "",
                "audience": "",
                "target_type": "individual",
                "city": "",
                "state": "",
                "country": "",
                "country_code": "",
                "keywords": [],
                "role_terms": [],
                "location_aliases": [],
            }
        return {"results": []}

    container = make_container(settings, FakeBackend(ROUTES), FakeLLM(handler))
    with client_for(container, settings) as client:
        response = client.post("/prospect-discovery", json={"query": "what is the capital of France"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_query"


def test_rate_limit_returns_429_with_retry_after():
    settings = make_settings(rate_limit_per_minute=2)
    container = make_container(settings, FakeBackend(ROUTES), None)
    with client_for(container, settings) as client:
        codes = [client.post("/prospect-discovery", json={"query": QUERY}).status_code for _ in range(3)]
        last = client.post("/prospect-discovery", json={"query": QUERY})
    assert codes == [200, 200, 429]
    assert last.status_code == 429
    assert int(last.headers["retry-after"]) >= 1


def test_api_key_is_enforced_when_configured():
    settings = make_settings(api_auth_token="s3cret-token")
    container = make_container(settings, FakeBackend(ROUTES), None)
    with client_for(container, settings) as client:
        missing = client.post("/prospect-discovery", json={"query": QUERY})
        wrong = client.post("/prospect-discovery", json={"query": QUERY}, headers={"X-API-Key": "nope"})
        right = client.post(
            "/prospect-discovery", json={"query": QUERY}, headers={"X-API-Key": "s3cret-token"}
        )
    assert missing.status_code == 401 and wrong.status_code == 401
    assert right.status_code == 200


def test_responses_are_cached(settings):
    backend = FakeBackend(ROUTES)
    container = make_container(settings, backend, None)
    with client_for(container, settings) as client:
        client.post("/prospect-discovery", json={"query": QUERY})
        first = len(backend.queries)
        client.post("/prospect-discovery", json={"query": QUERY.upper()})
    assert len(backend.queries) == first


def test_security_headers_health_and_docs(settings):
    container = make_container(settings, FakeBackend(ROUTES), None)
    with client_for(container, settings) as client:
        health = client.get("/health")
        docs = client.get("/docs")
        root = client.get("/", follow_redirects=False)
        missing = client.get("/nope")
    assert health.status_code == 200 and health.json()["search_provider"] == "fake"
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in health.headers["content-security-policy"]
    assert docs.status_code == 200
    assert root.status_code == 307 and root.headers["location"] == "/docs"
    assert missing.status_code == 404 and missing.json()["success"] is False


def test_unexpected_errors_do_not_leak_details(settings):
    container = make_container(settings, FakeBackend(ROUTES), None)

    async def boom(query, limit=None):
        raise RuntimeError("database password is hunter2")

    container.prospect_service.discover = boom  # type: ignore[method-assign]
    with TestClient(create_app(settings, container), raise_server_exceptions=False) as client:
        response = client.post("/prospect-discovery", json={"query": QUERY})
    assert response.status_code == 500
    assert "hunter2" not in response.text
    assert response.json()["error"]["code"] == "internal_error"
