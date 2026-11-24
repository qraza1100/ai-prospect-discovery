import httpx
import pytest

from app.errors import UnsafeURLError, UpstreamError
from app.utils.cache import AsyncTTLCache
from app.utils.http import RobotsPolicy, SafeFetcher, is_public_ip, send_with_retry
from tests.conftest import PUBLIC_IP


@pytest.mark.parametrize(
    ("ip", "public"),
    [
        ("93.184.216.34", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.3.4", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fd00::1", False),
        ("fe80::1%eth0", False),
        ("::ffff:127.0.0.1", False),
        ("2002:7f00:0001::", False),
        ("2606:4700:4700::1111", True),
        ("not-an-ip", False),
    ],
)
def test_is_public_ip(ip, public):
    assert is_public_ip(ip) is public


def make_fetcher(handler, resolved=PUBLIC_IP, max_bytes=100_000):
    async def resolver(host, port):
        return resolved if isinstance(resolved, list) else [resolved]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SafeFetcher(client, user_agent="TestBot/1.0", max_bytes=max_bytes, timeout=5, resolver=resolver)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://intranet/",
        "http://printer.local/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://user:pass@kickoff-dc.org/",
        "http://kickoff-dc.org:8080/",
        "file:///etc/passwd",
        "gopher://kickoff-dc.org/",
    ],
)
async def test_unsafe_urls_are_refused_before_any_request(url):
    def handler(request):
        raise AssertionError("no request should be sent")

    with pytest.raises(UnsafeURLError):
        await make_fetcher(handler).fetch(url)


async def test_hostname_resolving_to_private_address_is_refused():
    fetcher = make_fetcher(lambda r: httpx.Response(200), resolved=["93.184.216.34", "10.0.0.7"])
    with pytest.raises(UnsafeURLError):
        await fetcher.fetch("https://rebind.kickoff-dc.org/")


async def test_connection_is_pinned_to_checked_address():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, html="<html><title>ok</title></html>")

    result = await make_fetcher(handler).fetch("https://www.kickoff-dc.org/about")
    assert result.status == 200 and "ok" in (result.text or "")
    assert seen["url"] == f"https://{PUBLIC_IP}/about"
    assert seen["host"] == "www.kickoff-dc.org"
    assert seen["sni"] == "www.kickoff-dc.org"


async def test_redirect_to_internal_address_is_refused():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})

    with pytest.raises(UnsafeURLError):
        await make_fetcher(handler).fetch("https://www.kickoff-dc.org/")


async def test_redirects_are_followed_with_limit():
    def handler(request):
        return httpx.Response(301, headers={"location": "/again"})

    result = await make_fetcher(handler).fetch("https://www.kickoff-dc.org/")
    assert result.status is None and result.text is None


async def test_non_html_and_oversized_bodies():
    def pdf(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    result = await make_fetcher(pdf).fetch("https://www.kickoff-dc.org/file")
    assert result.text is None

    def big(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"a" * 50_000)

    result = await make_fetcher(big, max_bytes=1_000).fetch("https://www.kickoff-dc.org/")
    assert result.text is not None and len(result.text) == 1_000


@pytest.mark.parametrize(
    ("status", "body", "allowed"),
    [
        (200, "User-agent: *\nDisallow: /private", True),
        (200, "User-agent: TestBot\nDisallow: /", False),
        (404, "", True),
        (503, "", False),
    ],
)
async def test_robots_policy(status, body, allowed):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(status, text=body, headers={"content-type": "text/plain"})
        return httpx.Response(200, html="ok")

    robots = RobotsPolicy(make_fetcher(handler), "TestBot/1.0", AsyncTTLCache(8, 60))
    assert await robots.allowed("https://www.kickoff-dc.org/") is allowed


async def test_send_with_retry_retries_then_fails_cleanly():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError) as info:
        await send_with_retry(
            client, "GET", "https://api.test/x", service="svc", attempts=3, base_delay=0, max_delay=0
        )
    assert calls["n"] == 3
    assert info.value.status == 503


async def test_send_with_retry_does_not_retry_client_errors():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": "secret detail"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError) as info:
        await send_with_retry(client, "GET", "https://api.test/x", service="svc", base_delay=0)
    assert calls["n"] == 1
    assert "secret" not in info.value.safe_message


async def test_send_with_retry_recovers_after_rate_limit():
    responses = iter([httpx.Response(429, headers={"retry-after": "0"}), httpx.Response(200, json={"ok": 1})])
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: next(responses)))
    response = await send_with_retry(client, "GET", "https://api.test/x", service="svc", base_delay=0)
    assert response.json() == {"ok": 1}
