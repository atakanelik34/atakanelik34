"""Fixed-window rate limiter backed by Redis (shared across API replicas)."""

from __future__ import annotations

import hashlib

from redis.asyncio import Redis

from idp.domain.errors import RateLimitedError


class RedisRateLimiter:
    def __init__(self, redis: Redis, *, namespace: str, limit: int, window_seconds: int) -> None:
        self._redis = redis
        self._namespace = namespace
        self._limit = limit
        self._window = window_seconds

    def _key(self, identity: str) -> str:
        # Hash the identity so emails/IPs are not stored in Redis in clear text.
        digest = hashlib.sha256(identity.encode()).hexdigest()
        return f"ratelimit:{self._namespace}:{digest}"

    async def hit(self, identity: str) -> None:
        """Count one attempt; raise RateLimitedError when the window budget is spent."""
        key = self._key(identity)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self._window, nx=True)
            pipe.ttl(key)
            count, _, ttl = await pipe.execute()
        if int(count) > self._limit:
            raise RateLimitedError(
                "Too many attempts, try again later",
                retry_after_seconds=max(int(ttl), 1),
            )

    async def reset(self, identity: str) -> None:
        await self._redis.delete(self._key(identity))
