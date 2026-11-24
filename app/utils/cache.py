from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from functools import partial
from typing import Generic, TypeVar

T = TypeVar("T")


class AsyncTTLCache(Generic[T]):
    """In-process LRU cache with expiry and single-flight loading.

    Concurrent callers asking for the same missing key share one in-flight load, so an
    expensive upstream call runs once. Failed loads are not cached.
    """

    def __init__(self, maxsize: int, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._clock = clock
        self._data: OrderedDict[Hashable, tuple[float, T]] = OrderedDict()
        self._inflight: dict[Hashable, asyncio.Task[T]] = {}

    def get(self, key: Hashable) -> T | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= self._clock():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: Hashable, value: T) -> None:
        if self._ttl <= 0:
            return
        self._data[key] = (self._clock() + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    async def get_or_create(self, key: Hashable, factory: Callable[[], Awaitable[T]]) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(factory())
            self._inflight[key] = task
            task.add_done_callback(partial(self._settle, key))
        return await asyncio.shield(task)

    def _settle(self, key: Hashable, task: asyncio.Task[T]) -> None:
        self._inflight.pop(key, None)
        if not task.cancelled() and task.exception() is None:
            self.set(key, task.result())

    def __len__(self) -> int:
        return len(self._data)
