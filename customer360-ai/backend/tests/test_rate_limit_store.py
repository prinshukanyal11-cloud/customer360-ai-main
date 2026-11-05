"""Shared-store rate limiting.

The in-process counter is only correct for one API process. These tests pin the
part that was previously an accepted limitation: a shared counter store behind
`rate_limit_backend`, and a fallback that keeps a Redis outage from becoming an
API outage.

A fake Redis client is used rather than a live server so the tests are
deterministic and run in CI without a service container. It implements only the
commands `RedisCounterStore` issues, and mirrors their semantics (INCR, then
EXPIRE once, TTL) closely enough that a mistake in the store fails here.
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.middleware.rate_limit import (
    RateLimiter,
    RedisCounterStore,
)


class FakeRedis:
    """Enough of redis-py for the counter store, with real expiry semantics."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.calls: list[tuple] = []
        self.failing = False

    def _check(self) -> None:
        if self.failing:
            raise ConnectionError("redis is down")

    def incr(self, key: str) -> int:
        self._check()
        self.calls.append(("incr", key))
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        self._check()
        self.calls.append(("expire", key, seconds, nx))
        # nx: only set when the key has no TTL, which is what keeps a live window
        # from being extended by every request.
        if nx and key in self.ttls:
            return False
        self.ttls[key] = seconds
        return True

    def ttl(self, key: str) -> int:
        self._check()
        return self.ttls.get(key, -2)

    def scan_iter(self, match: str, count: int = 10):
        prefix = match.rstrip("*")
        for key in list(self.values):
            if key.startswith(prefix):
                yield key

    def delete(self, key: str) -> None:
        self._check()
        self.values.pop(key, None)
        self.ttls.pop(key, None)

    def ping(self) -> bool:
        self._check()
        return True

    def pipeline(self, transaction: bool = True) -> "FakePipeline":
        return FakePipeline(self)


class FakePipeline:
    """Queues commands and runs them in order, as redis-py does on execute()."""

    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._queued: list[tuple[str, tuple]] = []

    def incr(self, key: str) -> "FakePipeline":
        self._queued.append(("incr", (key,)))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> "FakePipeline":
        self._queued.append(("expire", (key, seconds, nx)))
        return self

    def execute(self) -> list:
        results = []
        for name, args in self._queued:
            results.append(getattr(self._client, name)(*args))
        self._queued.clear()
        return results


@pytest.fixture()
def fake_redis(monkeypatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr("app.core.config.get_redis_client", lambda: client)
    return client


# --- RedisCounterStore -------------------------------------------------------


def test_redis_store_counts_across_calls(fake_redis: FakeRedis) -> None:
    store = RedisCounterStore(fake_redis, prefix="ratelimit")
    assert store.hit("k", 3) == (True, 2, 0)
    assert store.hit("k", 3) == (True, 1, 0)
    assert store.hit("k", 3) == (True, 0, 0)

    allowed, remaining, retry_after = store.hit("k", 3)
    assert allowed is False
    assert remaining == 0
    assert retry_after >= 1


def test_redis_store_keys_are_independent(fake_redis: FakeRedis) -> None:
    store = RedisCounterStore(fake_redis, prefix="ratelimit")
    assert store.hit("a", 1)[0] is True
    assert store.hit("a", 1)[0] is False
    assert store.hit("b", 1)[0] is True


def test_redis_store_applies_a_ttl_once_per_window(fake_redis: FakeRedis) -> None:
    """Every request must not push the window forward.

    If the TTL were refreshed on each hit, a caller could stay under the limit
    indefinitely by never pausing.
    """
    store = RedisCounterStore(fake_redis, prefix="ratelimit")
    store.hit("k", 100)
    store.hit("k", 100)
    store.hit("k", 100)

    set_calls = [call for call in fake_redis.calls if call[0] == "expire"]
    assert len(set_calls) == 3  # issued every time...
    # ...but nx=True means only the first is applied.
    assert all(call[3] is True for call in set_calls)
    assert fake_redis.ttls["ratelimit:k"] == 60


def test_redis_store_namespaces_by_prefix(fake_redis: FakeRedis) -> None:
    RedisCounterStore(fake_redis, prefix="tenant-a").hit("same-key", 5)
    RedisCounterStore(fake_redis, prefix="tenant-b").hit("same-key", 5)
    assert set(fake_redis.values) == {"tenant-a:same-key", "tenant-b:same-key"}


def test_redis_store_reset_clears_prefix_only(fake_redis: FakeRedis) -> None:
    RedisCounterStore(fake_redis, prefix="mine").hit("k", 5)
    fake_redis.values["other:k"] = 9

    RedisCounterStore(fake_redis, prefix="mine").reset()
    assert "mine:k" not in fake_redis.values
    assert "other:k" in fake_redis.values


# --- backend selection -------------------------------------------------------


def test_memory_backend_never_touches_redis(fake_redis: FakeRedis, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_backend", "memory")
    limiter = RateLimiter()
    limiter.hit("k", 2)
    assert fake_redis.calls == []


def test_auto_backend_uses_redis_when_reachable(fake_redis: FakeRedis, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_backend", "auto")
    limiter = RateLimiter()

    limiter.hit("k", 2)
    assert fake_redis.values, "expected the counter to land in Redis"
    limiter.reset()
    assert fake_redis.values == {}


def test_auto_backend_falls_back_to_memory_when_redis_is_down(fake_redis: FakeRedis, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_backend", "auto")
    fake_redis.failing = True
    limiter = RateLimiter()

    # Must not raise: an unreachable counter store cannot become an API outage.
    allowed, _, _ = limiter.hit("k", 2)
    assert allowed is True
    # And it still limits, using the in-process store.
    assert limiter.hit("k", 2)[0] is True
    assert limiter.hit("k", 2)[0] is False


def test_redis_backend_fails_open_when_the_store_is_down(fake_redis: FakeRedis, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    fake_redis.failing = True
    limiter = RateLimiter()

    # Documented behaviour: requests are served rather than rejected, because
    # returning 429 to every caller because a counter store is unreachable would
    # turn a degraded control into a total one.
    allowed, _, _ = limiter.hit("k", 1)
    assert allowed is True


def test_backend_is_resolved_per_call(fake_redis: FakeRedis, monkeypatch) -> None:
    """A config change must take effect without rebuilding the middleware."""
    limiter = RateLimiter()
    monkeypatch.setattr(settings, "rate_limit_backend", "memory")
    limiter.hit("shared", 5)
    assert fake_redis.calls == []

    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    limiter.hit("shared", 5)
    assert fake_redis.calls, "switching backends must re-resolve"


def test_memory_store_is_the_default_for_a_bare_process(monkeypatch) -> None:
    """auto must not require Redis to be present at all."""
    def _no_redis():
        raise ConnectionError("no redis here")

    monkeypatch.setattr("app.core.config.get_redis_client", _no_redis)
    monkeypatch.setattr(settings, "rate_limit_backend", "auto")
    limiter = RateLimiter()
    assert limiter.hit("k", 1)[0] is True
    assert limiter.hit("k", 1)[0] is False


def test_redis_is_not_reprobed_on_every_request_during_an_outage(monkeypatch) -> None:
    """An outage must not become a connection attempt per request."""
    attempts = {"n": 0}

    def _counting_redis():
        attempts["n"] += 1
        raise ConnectionError("redis is down")

    monkeypatch.setattr("app.core.config.get_redis_client", _counting_redis)
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    limiter = RateLimiter()

    for _ in range(5):
        limiter.hit("k", 10)

    assert attempts["n"] == 1
    assert limiter._redis is None


def test_rate_limiting_survives_a_redis_recovery(fake_redis: FakeRedis, monkeypatch) -> None:
    """After Redis comes back, counters resume there instead of staying local."""
    monkeypatch.setattr(settings, "rate_limit_backend", "redis")
    limiter = RateLimiter()
    limiter._last_probe_at = 0.0  # outside the probe interval

    # First probe fails against a dead server.
    fake_redis.failing = True
    assert limiter.hit("k", 1)[0] is True
    assert limiter._redis is None

    # Redis recovers and the probe interval elapses.
    fake_redis.failing = False
    limiter._last_probe_at = 0.0
    assert limiter.hit("k", 1)[0] is True
    assert limiter._redis is not None
    assert fake_redis.values, "counters must resume in the shared store"