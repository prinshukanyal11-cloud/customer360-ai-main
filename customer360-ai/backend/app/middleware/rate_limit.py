"""Fixed-window rate limiting with a pluggable counter store.

`rate_limit_enabled` / `rate_limit_per_minute` existed in config and were listed
in `.env.example` and `docs/security.md`, but nothing read them: there was no
limiter anywhere in the app. This module implements the control the docs already
promised, aimed at the two abuse cases that matter here - credential stuffing on
`/auth/login` and a single caller looping expensive exports or model scans.

Where the counters live
-----------------------
`rate_limit_backend` selects the store:

* ``memory`` - per process. Simple, no dependency, and correct for a single API
  process or local development.
* ``redis`` - shared, so the budget is a property of the deployment rather than
  of a pod. Required once more than one replica serves traffic, otherwise N
  replicas grant N times the configured rate.
* ``auto`` (default) - Redis when it is reachable, memory otherwise.

The ``auto`` fallback exists because the alternative is worse: a Redis outage
must not become an API outage. It is a genuine reduction in protection during the
outage and is logged as an error, not silently swallowed.

Scope, stated plainly so it is not over-claimed:
- The window is fixed, not sliding, so a caller can spend 2x the budget across a
  window boundary. Fixed windows are the right trade for a coarse guard.
- Authentication is not required to be rate limited, because the endpoint that
  needs it most - login - is reachable without a token.
- A Redis outage in ``redis``/``auto`` mode fails *open*: requests are served
  rather than rejected, because returning 429 to every caller because a counter
  store is unreachable would turn a degraded control into a total one.
"""
from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.logging_config import get_logger

WINDOW_SECONDS = 60
# Prune lazily rather than on a timer thread: a background sweeper would be a
# second thing that can die silently and leak memory.
PRUNE_AFTER_ENTRIES = 10_000


@dataclass
class _Window:
    count: int
    expires_at: float


class CounterStore(Protocol):
    def hit(self, key: str, limit: int) -> tuple[bool, int, int]:
        """Register a request. Returns (allowed, remaining, retry_after_seconds)."""
        ...


class FixedWindowLimiter:
    """In-process counter. Thread-safe, prunes expired keys when it grows."""

    def __init__(self, window_seconds: float = WINDOW_SECONDS) -> None:
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()
        self._window_seconds = window_seconds

    def hit(self, key: str, limit: int, now: float | None = None) -> tuple[bool, int, int]:
        moment = time.monotonic() if now is None else now
        with self._lock:
            # Swept before the early return below: a stream of never-seen keys (many
            # distinct forwarded IPs) is exactly the case that would otherwise grow
            # the dict forever, because those keys never take the update branch.
            if len(self._windows) > PRUNE_AFTER_ENTRIES:
                self._windows = {k: w for k, w in self._windows.items() if w.expires_at > moment}

            window = self._windows.get(key)
            if window is None or window.expires_at <= moment:
                self._windows[key] = _Window(count=1, expires_at=moment + self._window_seconds)
                return True, max(limit - 1, 0), 0

            window.count += 1
            if window.count > limit:
                return False, 0, max(int(window.expires_at - moment), 1)
            return True, max(limit - window.count, 0), 0

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


class RedisCounterStore:
    """Shared fixed-window counters in Redis (INCR + EXPIRE)."""

    def __init__(self, client, *, prefix: str = "ratelimit", window_seconds: int = WINDOW_SECONDS) -> None:
        self._client = client
        self._prefix = prefix
        self._window_seconds = window_seconds

    def _redis_key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def hit(self, key: str, limit: int) -> tuple[bool, int, int]:
        redis_key = self._redis_key(key)
        # INCR then, on first use, set the expiry. Doing both in one pipeline keeps
        # the two round trips to one and stops a crash between them from leaving a
        # key with no TTL - which would pin the caller at 1 request/forever.
        pipe = self._client.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, self._window_seconds, nx=True)
        count, _ = pipe.execute()
        count = int(count)

        if count > limit:
            ttl = int(self._client.ttl(redis_key))
            return False, 0, max(ttl, 1)
        return True, max(limit - count, 0), 0

    def reset(self) -> None:
        # Only used by tests; SCAN rather than KEYS so this cannot block Redis.
        for found in self._client.scan_iter(match=f"{self._prefix}:*", count=100):
            self._client.delete(found)


class RateLimiter:
    """Facade that resolves the configured store per call.

    Resolution is deferred rather than resolved once at import, so a test that
    flips `rate_limit_backend` takes effect immediately and a Redis connection is
    not opened merely because the module was imported.
    """

    def __init__(self) -> None:
        self._memory = FixedWindowLimiter()
        self._redis: RedisCounterStore | None = None
        # When the last connection attempt was made, successful or not. The probe
        # interval is what stops an outage from turning into a connection attempt
        # on every single request.
        self._last_probe_at: float = 0.0
        self._redis_retry_after: float = 5.0
        self._lock = threading.Lock()

    def _store(self) -> tuple[CounterStore, bool]:
        """Return (store, is_degraded). Re-probes Redis at most every few seconds."""
        backend = (settings.rate_limit_backend or "auto").strip().lower()
        if backend == "memory":
            return self._memory, False

        now = time.monotonic()
        if now - self._last_probe_at < self._redis_retry_after:
            if self._redis is not None:
                return self._redis, False
            return self._memory, True

        with self._lock:
            if now - self._last_probe_at < self._redis_retry_after:
                # Another thread settled it while this one waited on the lock.
                if self._redis is not None:
                    return self._redis, False
                return self._memory, True

            try:
                store = self._connect()
            except Exception as exc:  # noqa: BLE001 - any client error means the same thing here
                self._redis = None
                self._last_probe_at = now
                if backend == "redis":
                    # Explicitly configured but unreachable: fail open rather than
                    # reject every caller, and say so loudly.
                    get_logger().error(
                        "rate_limit_redis_unavailable_falling_back_to_memory",
                        error=str(exc),
                        backend=backend,
                    )
                return self._memory, True

            self._redis = store
            self._last_probe_at = now
            return store, False

    @staticmethod
    def _connect() -> RedisCounterStore:
        from app.core.config import get_redis_client

        client = get_redis_client()
        # Fail fast if Redis is not actually answering, rather than on the first
        # real request from a user.
        client.ping()
        return RedisCounterStore(
            client,
            prefix=settings.rate_limit_redis_prefix or "ratelimit",
            window_seconds=WINDOW_SECONDS,
        )

    def hit(self, key: str, limit: int, now: float | None = None) -> tuple[bool, int, int]:
        store, _degraded = self._store()
        if store is self._memory:
            # Only the in-process store accepts an injected clock (tests); the
            # Redis store keeps its own atomic server-side window.
            return store.hit(key, limit, now)  # type: ignore[call-arg]
        return store.hit(key, limit)

    def reset(self) -> None:
        self._memory.reset()
        if self._redis is not None:
            # Best-effort: reset is used by tests and by nothing on the request
            # path, so an unreachable Redis must not raise out of it.
            with contextlib.suppress(Exception):
                self._redis.reset()


limiter = RateLimiter()


def _client_key(request: Request) -> str:
    """Identify the caller.

    Only honours X-Forwarded-For when explicitly configured. Trusting it by
    default would let any direct caller set the header to a random value and
    escape the limit entirely - the limiter would become a no-op against anyone
    who read the docs.
    """
    if settings.rate_limit_trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _limit_for(path: str) -> int:
    """Pick the budget for a request.

    The comparison is made against the path *inside* the versioned prefix:
    routers are mounted at /api/v1, so testing the raw path against "/auth/"
    would silently hand login the general budget instead of the tighter one.
    """
    prefix = settings.api_v1_prefix
    relative = path[len(prefix) :] if path.startswith(prefix) else path
    return settings.auth_rate_limit_per_minute if relative.startswith("/auth/") else settings.rate_limit_per_minute


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        if not path.startswith(settings.api_v1_prefix):
            return await call_next(request)
        if any(path.endswith(entry) for entry in settings.rate_limit_exempt_path_list):
            return await call_next(request)

        limit = _limit_for(path)
        allowed, remaining, retry_after = limiter.hit(f"{limit}:{_client_key(request)}", limit)
        if not allowed:
            get_logger().warning(
                "rate_limited", path=path, client=_client_key(request), limit=limit, retry_after=retry_after
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests. Please retry after the cooldown.",
                        "details": {"limit": limit, "retry_after_seconds": retry_after},
                    }
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response