from __future__ import annotations

import asyncio
import ipaddress
import random
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any
from urllib.robotparser import RobotFileParser

import httpx

from app.errors import UnsafeURLError, UpstreamError
from app.utils.cache import AsyncTTLCache

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
HTML_TYPES = ("text/html", "application/xhtml+xml")
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".corp")

Resolver = Callable[[str, int], Awaitable[list[str]]]


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


async def send_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    service: str,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request, retrying timeouts, connection errors, 429 and 5xx with
    exponential backoff and jitter. Retry-After is honoured up to max_delay."""
    last_error = "request failed"
    last_status: int | None = None
    for attempt in range(1, attempts + 1):
        wait: float | None = None
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException:
            last_error, last_status = "timed out", None
        except httpx.TransportError:
            last_error, last_status = "connection failed", None
        else:
            if response.status_code < 400:
                return response
            last_status = response.status_code
            if response.status_code in (401, 403):
                raise UpstreamError(service, "credentials rejected or quota exhausted", last_status)
            if response.status_code not in RETRYABLE_STATUS:
                raise UpstreamError(service, f"HTTP {response.status_code}", last_status)
            last_error = "rate limited" if response.status_code == 429 else f"HTTP {last_status}"
            wait = _retry_after_seconds(response)
        if attempt == attempts:
            break
        backoff = base_delay * 2 ** (attempt - 1) + random.uniform(0, base_delay)  # noqa: S311
        await asyncio.sleep(min(max_delay, wait if wait is not None else backoff))
    raise UpstreamError(service, last_error, last_status)


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped or address.sixtofour
        if mapped is not None:
            address = mapped
    return address.is_global and not address.is_multicast


def _ascii_host(url: httpx.URL) -> str:
    """IDNA form of the host, which is what DNS, the Host header and TLS SNI expect."""
    return url.raw_host.decode("ascii")


@dataclass(slots=True)
class FetchResult:
    status: int | None
    url: str
    content_type: str = ""
    text: str | None = None


class SafeFetcher:
    """Fetches third-party pages without exposing internal networks.

    Every hop (including redirects) is resolved first and refused unless all of its addresses
    are public. The connection is then pinned to the checked address, with the original host
    sent as Host header and TLS SNI, so a second DNS answer cannot redirect it (rebinding).
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str,
        max_bytes: int,
        timeout: float,
        resolver: Resolver = system_resolver,
        max_redirects: int = 3,
    ) -> None:
        self._client = client
        self._user_agent = user_agent
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._resolver = resolver
        self._max_redirects = max_redirects

    @staticmethod
    def build_client(timeout: float) -> httpx.AsyncClient:
        no_cookies = CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            cookies=httpx.Cookies(no_cookies),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def resolve_public_address(self, url: httpx.URL) -> str:
        if url.scheme not in ("http", "https"):
            raise UnsafeURLError("only http and https URLs are allowed")
        if url.userinfo:
            raise UnsafeURLError("credentials in URLs are not allowed")
        host = _ascii_host(url).rstrip(".").lower()
        if not host:
            raise UnsafeURLError("URL has no host")
        port = url.port or (443 if url.scheme == "https" else 80)
        if port not in (80, 443):
            raise UnsafeURLError("non-standard ports are not allowed")
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            if "." not in host or host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
                raise UnsafeURLError("internal host names are not allowed") from None
            try:
                addresses = await self._resolver(host, port)
            except OSError as exc:
                raise UnsafeURLError("host could not be resolved") from exc
        if not addresses or not all(is_public_ip(a) for a in addresses):
            raise UnsafeURLError("host resolves to a non-public address")
        return addresses[0]

    def _pinned_request(self, url: httpx.URL, address: str) -> httpx.Request:
        host = _ascii_host(url)
        host_header = host if url.port is None else f"{host}:{url.port}"
        extensions: dict[str, Any] = {}
        if url.scheme == "https":
            extensions["sni_hostname"] = host
        return self._client.build_request(
            "GET",
            url.copy_with(host=address),
            headers={
                "Host": host_header,
                "User-Agent": self._user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.5",
                "Accept-Language": "en;q=0.9,*;q=0.5",
                "Accept-Encoding": "identity",
            },
            timeout=self._timeout,
            extensions=extensions,
        )

    async def fetch(self, url: str, accept: Iterable[str] | None = HTML_TYPES) -> FetchResult:
        """Fetch one page. The whole exchange, redirects included, must finish within twice
        the per-request timeout, so a server that trickles bytes cannot hold a slot open."""
        try:
            async with asyncio.timeout(self._timeout * 2):
                return await self._fetch(url, accept)
        except TimeoutError:
            return FetchResult(status=None, url=url)

    async def _fetch(self, url: str, accept: Iterable[str] | None) -> FetchResult:
        allowed_types = tuple(accept) if accept is not None else None
        current = httpx.URL(url)
        for _ in range(self._max_redirects + 1):
            address = await self.resolve_public_address(current)
            try:
                response = await self._client.send(self._pinned_request(current, address), stream=True)
            except httpx.HTTPError:
                return FetchResult(status=None, url=str(current))
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return FetchResult(status=response.status_code, url=str(current))
                    current = current.join(location)
                    continue
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                result = FetchResult(response.status_code, str(current), content_type.lower())
                if response.status_code != 200:
                    return result
                if allowed_types is not None and result.content_type not in allowed_types:
                    return result
                body = await self._read_limited(response)
                encoding = response.charset_encoding or "utf-8"
                try:
                    result.text = body.decode(encoding, errors="replace")
                except LookupError:
                    result.text = body.decode("utf-8", errors="replace")
                return result
            except httpx.HTTPError:
                return FetchResult(status=None, url=str(current))
            finally:
                await response.aclose()
        return FetchResult(status=None, url=str(current))

    async def _read_limited(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            remaining = self._max_bytes - total
            if remaining <= 0:
                break
            chunks.append(chunk[:remaining])
            total += len(chunks[-1])
        return b"".join(chunks)


class RobotsPolicy:
    """robots.txt checks following RFC 9309: a 4xx answer allows everything, while a
    5xx answer or an unreachable host disallows everything."""

    def __init__(self, fetcher: SafeFetcher, user_agent: str, cache: AsyncTTLCache[Any]) -> None:
        self._fetcher = fetcher
        self._agent = user_agent.split("/", 1)[0]
        self._cache = cache

    async def allowed(self, url: str) -> bool:
        parsed = httpx.URL(url)
        origin = f"{parsed.scheme}://{parsed.host}"
        rules = await self._cache.get_or_create(origin, lambda: self._load(origin))
        if rules == "allow":
            return True
        if rules == "deny":
            return False
        return bool(rules.can_fetch(self._agent, url))

    async def _load(self, origin: str) -> RobotFileParser | str:
        try:
            result = await self._fetcher.fetch(f"{origin}/robots.txt", accept=None)
        except UnsafeURLError:
            return "deny"
        if result.status == 200 and result.text is not None:
            parser = RobotFileParser()
            parser.parse(result.text.splitlines())
            return parser
        if result.status is not None and 400 <= result.status < 500:
            return "allow"
        return "deny"
