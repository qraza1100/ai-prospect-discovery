import pytest

from app.platforms.google import GoogleAdapter, is_listicle_title
from app.platforms.registry import PlatformRegistry
from app.schemas import Platform

registry = PlatformRegistry.default()


@pytest.mark.parametrize(
    ("url", "platform", "canonical", "handle"),
    [
        (
            "https://uk.linkedin.com/in/Jane-Roe-1a2b3c?trk=x",
            Platform.LINKEDIN,
            "https://www.linkedin.com/in/jane-roe-1a2b3c",
            "jane-roe-1a2b3c",
        ),
        (
            "https://www.linkedin.com/company/kickoff-media/about/",
            Platform.LINKEDIN,
            "https://www.linkedin.com/company/kickoff-media",
            "kickoff-media",
        ),
        (
            "https://www.instagram.com/DCKickoffFans/",
            Platform.INSTAGRAM,
            "https://www.instagram.com/dckickofffans/",
            "dckickofffans",
        ),
        (
            "https://instagram.com/dckickofffans/p/Cx12AbC/",
            Platform.INSTAGRAM,
            "https://www.instagram.com/dckickofffans/",
            "dckickofffans",
        ),
        ("https://twitter.com/DCKickoff/status/123456", Platform.X, "https://x.com/dckickoff", "dckickoff"),
        ("https://x.com/dckickoff", Platform.X, "https://x.com/dckickoff", "dckickoff"),
        (
            "https://m.facebook.com/DCKickoffClub/posts/987",
            Platform.FACEBOOK,
            "https://www.facebook.com/dckickoffclub",
            "dckickoffclub",
        ),
        (
            "https://www.facebook.com/profile.php?id=100012345678",
            Platform.FACEBOOK,
            "https://www.facebook.com/profile.php?id=100012345678",
            "100012345678",
        ),
        (
            "https://www.threads.net/@dckickofffans/post/C9x",
            Platform.THREADS,
            "https://www.threads.com/@dckickofffans",
            "dckickofffans",
        ),
        (
            "https://www.youtube.com/@DCKickoff/videos",
            Platform.YOUTUBE,
            "https://www.youtube.com/@dckickoff",
            "dckickoff",
        ),
        (
            "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
            Platform.YOUTUBE,
            "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
            None,
        ),
        ("https://www.kickoff-dc.org/blog/match-day", Platform.GOOGLE, "https://www.kickoff-dc.org/", None),
    ],
)
def test_profile_urls_are_canonicalised(url, platform, canonical, handle):
    routed = registry.parse(url)
    assert routed is not None
    parsed = routed[1]
    assert parsed.platform == platform
    assert parsed.url == canonical
    assert parsed.handle == handle


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/Cx12AbC/",
        "https://www.instagram.com/explore/tags/football/",
        "https://x.com/search?q=football",
        "https://x.com/i/lists/123",
        "https://www.facebook.com/groups/dcfootball/",
        "https://www.facebook.com/sharer/sharer.php?u=x",
        "https://www.linkedin.com/pulse/some-article",
        "https://www.linkedin.com/posts/jane_activity-1",
        "https://www.youtube.com/watch?v=abc",
        "https://www.threads.com/search",
        "https://www.yelp.com/biz/some-restaurant",
        "https://en.wikipedia.org/wiki/Football",
        "https://www.dc.gov/page/sports",
        "https://wa.me/971501234567",
        "ftp://kickoff-dc.org/",
    ],
)
def test_non_profile_urls_are_rejected(url):
    assert registry.parse(url) is None


@pytest.mark.parametrize(
    ("url", "title", "expected"),
    [
        (
            "https://www.linkedin.com/in/jane-roe",
            "Jane Roe - Youth Football Coach - DC United | LinkedIn",
            "Jane Roe",
        ),
        (
            "https://www.instagram.com/janeroe/",
            "Jane Roe ⚽ (@janeroe) • Instagram photos and videos",
            "Jane Roe",
        ),
        ("https://www.instagram.com/janeroe/", "@janeroe • Instagram photos and videos", "@janeroe"),
        ("https://x.com/janeroe", "Jane Roe (@janeroe) / X", "Jane Roe"),
        ("https://x.com/janeroe/status/1", 'Jane Roe on X: "Big win today" / X', "Jane Roe"),
        ("https://www.facebook.com/kickoffdc", "Kickoff DC Supporters | Facebook", "Kickoff DC Supporters"),
        ("https://www.threads.com/@janeroe", "Jane Roe (@janeroe) • Threads, Say more", "Jane Roe"),
        ("https://www.youtube.com/@janeroe", "Jane Roe Football - YouTube", "Jane Roe Football"),
        ("https://www.kickoff-dc.org/", "Home | Kickoff DC Supporters Club", "Kickoff DC Supporters Club"),
    ],
)
def test_names_come_from_titles(url, title, expected):
    adapter, parsed = registry.parse(url)
    assert adapter.extract_name(title, parsed) == expected


def test_listicle_titles_are_detected():
    assert is_listicle_title("Top 10 Digital Marketing Agencies in New York")
    assert is_listicle_title("15 Best Restaurants in Dubai (2026)")
    assert not is_listicle_title("Kickoff Media | Digital Marketing Agency NYC")


def test_queries_are_site_restricted_and_use_intent(intent):
    linkedin = registry.get(Platform.LINKEDIN).build_queries(intent)
    assert linkedin[0] == 'site:linkedin.com/in ("Football" OR "NFL") "Washington DC"'
    x_query = registry.get(Platform.X).build_queries(intent)[0]
    assert x_query.startswith("(site:x.com OR site:twitter.com)")
    google = GoogleAdapter().build_queries(intent)[0]
    assert "-site:linkedin.com" in google and '"Washington DC"' in google
    for adapter in registry.adapters:
        for query in adapter.build_queries(intent):
            assert "Football" in query or "NFL" in query


def test_business_queries_include_company_pages(intent):
    business = intent.model_copy(update={"target_type": "business", "interest": "Restaurant"})
    queries = registry.get(Platform.LINKEDIN).build_queries(business)
    assert any(q.startswith("site:linkedin.com/company") for q in queries)
