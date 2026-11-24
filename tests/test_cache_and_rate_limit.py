import asyncio

import pytest

from app.utils.cache import AsyncTTLCache
from app.utils.rate_limit import SlidingWindowRateLimiter


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


async def test_cache_single_flight_and_expiry():
    clock = Clock()
    cache: AsyncTTLCache[int] = AsyncTTLCache(maxsize=4, ttl=10, clock=clock)
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return 42

    results = await asyncio.gather(*(cache.get_or_create("k", load) for _ in range(5)))
    assert results == [42] * 5 and calls == 1
    clock.now = 11
    await cache.get_or_create("k", load)
    assert calls == 2


async def test_cache_does_not_store_failures():
    cache: AsyncTTLCache[int] = AsyncTTLCache(maxsize=4, ttl=10)

    async def fail():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await cache.get_or_create("k", fail)
    assert cache.get("k") is None


def test_cache_evicts_least_recently_used():
    cache: AsyncTTLCache[int] = AsyncTTLCache(maxsize=2, ttl=10)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")
    cache.set("c", 3)
    assert cache.get("b") is None and cache.get("a") == 1 and len(cache) == 2


def test_rate_limiter_window():
    clock = Clock()
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, clock=clock)
    assert limiter.check("ip")[0]
    assert limiter.check("ip")[0]
    allowed, retry = limiter.check("ip")
    assert not allowed and retry == 60
    assert limiter.check("other")[0]
    clock.now = 61
    assert limiter.check("ip")[0]


def test_rate_limiter_disabled():
    limiter = SlidingWindowRateLimiter(limit=0)
    assert all(limiter.check("ip")[0] for _ in range(100))
