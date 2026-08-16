"""Shared test fixtures.

Constitution IV requires the boundaries to external systems to be mocked here rather than
re-invented per test module, and forbids any test contacting a real service. Redis is the
boundary that shows up first: sessions (T020) and rate limiting (T022) both live on it.

`FakeRedis` is a small in-memory stand-in implementing only the commands this project
actually uses. It is deliberately not a general Redis emulator — an incomplete fake that
fails loudly on an unimplemented command is safer than a plausible one that quietly
disagrees with the real server.

Time is explicit rather than wall-clock, per Constitution IV's determinism rule: `FakeClock`
starts at a fixed instant and only moves when a test moves it, so an expiry test is exact
instead of a race against `sleep`.
"""

import fnmatch
from collections.abc import AsyncIterator

import pytest


class FakeClock:
    """A clock that only advances when a test tells it to."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self._now = now

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeRedis:
    """In-memory stand-in for the subset of `redis.asyncio` this project uses.

    Mirrors the real client's `decode_responses=True` configuration in
    `app/common/redis.py`, so values come back as `str` exactly as they do in production.
    """

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.clock = clock or FakeClock()
        self._values: dict[str, str] = {}
        self._expires_at: dict[str, float] = {}

    # -- expiry bookkeeping -------------------------------------------------

    def _expired(self, key: str) -> bool:
        deadline = self._expires_at.get(key)
        return deadline is not None and self.clock.time() >= deadline

    def _drop_if_expired(self, key: str) -> None:
        if self._expired(key):
            self._values.pop(key, None)
            self._expires_at.pop(key, None)

    # -- commands -----------------------------------------------------------

    async def get(self, key: str) -> str | None:
        self._drop_if_expired(key)
        return self._values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._values[key] = str(value)
        if ex is None:
            self._expires_at.pop(key, None)
        else:
            self._expires_at[key] = self.clock.time() + ex
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            self._drop_if_expired(key)
            if self._values.pop(key, None) is not None:
                self._expires_at.pop(key, None)
                removed += 1
        return removed

    async def exists(self, key: str) -> int:
        self._drop_if_expired(key)
        return 1 if key in self._values else 0

    async def scan_iter(self, match: str | None = None) -> AsyncIterator[str]:
        for key in list(self._values):
            self._drop_if_expired(key)
            if key in self._values and (match is None or fnmatch.fnmatch(key, match)):
                yield key

    async def incr(self, key: str) -> int:
        self._drop_if_expired(key)
        current = int(self._values.get(key, "0")) + 1
        self._values[key] = str(current)
        return current

    async def expire(self, key: str, seconds: int) -> bool:
        self._drop_if_expired(key)
        if key not in self._values:
            return False
        self._expires_at[key] = self.clock.time() + seconds
        return True

    async def ttl(self, key: str) -> int:
        """Seconds remaining, or the real client's sentinels: -2 unknown, -1 no expiry."""
        self._drop_if_expired(key)
        if key not in self._values:
            return -2
        if key not in self._expires_at:
            return -1
        return int(self._expires_at[key] - self.clock.time())


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_redis(clock: FakeClock) -> FakeRedis:
    return FakeRedis(clock)
