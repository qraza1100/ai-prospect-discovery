from app.platforms.registry import PlatformRegistry
from app.schemas import ContactSet, Platform, ProfileCandidate, SearchHit
from app.services.deduplication_service import DeduplicationService, same_identity
from app.services.profile_extractor import CandidateFactory, parse_page

registry = PlatformRegistry.default()

SITE_HTML = """
<html><head>
<title>Home | Kickoff DC Supporters Club</title>
<meta property="og:site_name" content="Kickoff DC Supporters Club">
<meta name="description" content="Washington DC football supporters club. Match-day meetups in the DMV.">
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "SportsClub", "name": "Kickoff DC",
 "telephone": "+1 202-456-1111",
 "sameAs": ["https://www.instagram.com/dckickofffans/", "https://x.com/DCKickoff"],
 "address": {"@type": "PostalAddress", "addressLocality": "Washington", "addressRegion": "DC", "addressCountry": "US"}}
</script>
</head><body>
<nav><a href="/contact-us">Contact</a></nav>
<p>Email us at <a href="mailto:hello@kickoff-dc.org">hello@kickoff-dc.org</a></p>
<a href="https://wa.me/12024561111">Chat on WhatsApp</a>
<a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>
<a href="https://www.facebook.com/dckickoffclub">Facebook</a>
<footer>Built by <a href="https://www.instagram.com/some.designer/">a designer</a></footer>
<script>var x = "tracking@vendor-analytics.com";</script>
</body></html>
"""


def test_parse_page_collects_owner_published_facts():
    facts = parse_page(SITE_HTML, "https://www.kickoff-dc.org/", registry, "us")
    assert facts.site_name == "Kickoff DC Supporters Club"
    assert "football supporters" in facts.description
    assert facts.location == "Washington, DC, US"
    assert facts.contacts.emails == {"hello@kickoff-dc.org"}
    assert facts.contacts.phones == {"+12024561111"}
    assert facts.contacts.whatsapp == {"+12024561111"}
    assert facts.contact_page == "https://www.kickoff-dc.org/contact-us"
    assert "https://www.facebook.com/dckickoffclub" in facts.social_urls
    assert "https://x.com/dckickoff" in facts.social_urls
    assert "https://www.instagram.com/dckickofffans/" in facts.social_urls
    assert "https://www.instagram.com/some.designer/" not in facts.social_urls
    assert not any("sharer" in u for u in facts.social_urls)


def test_parse_page_drops_directory_style_link_lists():
    links = "".join(f'<a href="https://www.instagram.com/member{i}/">m</a>' for i in range(6))
    facts = parse_page(f"<html><body>{links}</body></html>", "https://fans-list.org/", registry, None)
    assert facts.social_urls == set()


def test_parse_page_drops_mass_email_lists():
    emails = " ".join(f"person{i}@mailbox-{i}.org" for i in range(8))
    facts = parse_page(f"<html><body><p>{emails}</p></body></html>", "https://list.org/", registry, None)
    assert facts.contacts.emails == set()


def hit(url, title, snippet, source=Platform.GOOGLE):
    return SearchHit(url=url, title=title, snippet=snippet, query="q", source=source)


def test_candidate_factory_merges_repeated_hits_and_reads_snippet_contacts():
    hits = [
        hit(
            "https://www.instagram.com/dckickofffans/",
            "Kickoff DC (@dckickofffans) • Instagram photos and videos",
            "DC football fans ⚽ bookings: hello@kickoff-dc.org",
            Platform.INSTAGRAM,
        ),
        hit(
            "https://instagram.com/DCKickoffFans/p/abc/",
            "Kickoff DC on Instagram",
            "Match day in Washington DC",
            Platform.EMAIL,
        ),
        hit("https://www.instagram.com/p/xyz/", "post", "no handle in URL"),
        hit("https://www.kickoff-dc.org/news", "Top 10 football bars in DC", "listicle"),
    ]
    candidates = CandidateFactory(registry).build(hits, "us")
    assert len(candidates) == 1
    only = candidates[0]
    assert only.url == "https://www.instagram.com/dckickofffans/"
    assert only.name == "Kickoff DC"
    assert only.contacts.emails == {"hello@kickoff-dc.org"}
    assert only.sources == {"Instagram", "Email"}
    assert "Match day" in only.snippet


def cand(cid, platform, url, handle=None, name=None, **kw):
    return ProfileCandidate(
        id=cid, platform=platform, url=url, handle=handle, name=name, kind="unknown", **kw
    )


def test_same_handle_and_name_merge_across_platforms():
    ig = cand("1", Platform.INSTAGRAM, "https://www.instagram.com/janeroe/", "janeroe", "Jane Roe")
    x = cand("2", Platform.X, "https://x.com/janeroe", "janeroe", "Jane Roe")
    assert same_identity(ig, x)


def test_same_name_alone_never_merges():
    a = cand("1", Platform.LINKEDIN, "https://www.linkedin.com/in/john-smith-1", "john-smith-1", "John Smith")
    b = cand("2", Platform.INSTAGRAM, "https://www.instagram.com/jsmith/", "jsmith", "John Smith")
    assert not same_identity(a, b)


def test_same_handle_with_different_names_does_not_merge():
    a = cand("1", Platform.X, "https://x.com/alexdc", "alexdc", "Alex Turner")
    b = cand("2", Platform.FACEBOOK, "https://www.facebook.com/alexdc", "alexdc", "Alexandra Moore")
    assert not same_identity(a, b)


def test_instagram_and_threads_share_handles():
    ig = cand("1", Platform.INSTAGRAM, "https://www.instagram.com/janeroe/", "janeroe", "Jane R.")
    th = cand("2", Platform.THREADS, "https://www.threads.com/@janeroe", "janeroe", None)
    assert same_identity(ig, th)


def test_cluster_uses_website_links_and_shared_contacts():
    site = cand(
        "1",
        Platform.GOOGLE,
        "https://www.kickoff-dc.org/",
        None,
        "Kickoff DC",
        linked_urls={"https://www.instagram.com/dckickofffans/"},
    )
    ig = cand(
        "2", Platform.INSTAGRAM, "https://www.instagram.com/dckickofffans/", "dckickofffans", "Kickoff DC"
    )
    fb = cand(
        "3",
        Platform.FACEBOOK,
        "https://www.facebook.com/kickoffdcclub",
        "kickoffdcclub",
        "KDC",
        contacts=ContactSet(emails={"hello@kickoff-dc.org"}),
    )
    li = cand(
        "4",
        Platform.LINKEDIN,
        "https://www.linkedin.com/company/kickoff-dc",
        "kickoff-dc",
        "Kickoff DC",
        contacts=ContactSet(emails={"hello@kickoff-dc.org"}),
    )
    other = cand(
        "5", Platform.INSTAGRAM, "https://www.instagram.com/someoneelse/", "someoneelse", "Kickoff DC"
    )
    clusters = DeduplicationService().cluster([site, ig, fb, li, other])
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2, 2]
    merged_ids = {frozenset(m.id for m in c) for c in clusters}
    assert frozenset({"1", "2"}) in merged_ids
    assert frozenset({"3", "4"}) in merged_ids


def test_cluster_refuses_two_accounts_on_one_platform():
    a = cand("1", Platform.INSTAGRAM, "https://www.instagram.com/janeroe/", "janeroe", "Jane Roe")
    b = cand("2", Platform.X, "https://x.com/janeroe", "janeroe", "Jane Roe")
    c = cand("3", Platform.X, "https://x.com/jane_roe", "jane_roe", "Jane Roe")
    clusters = DeduplicationService().cluster([a, b, c])
    for cluster in clusters:
        platforms = [m.platform for m in cluster]
        assert len(platforms) == len(set(platforms))
