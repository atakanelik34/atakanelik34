"""Worker presence: an independent heartbeat loop.

Runs as its own asyncio task, not as a queued job, so a worker whose job slots
are all busy (e.g. long OCR runs) still reports itself alive.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import replace

from redis.asyncio import Redis

from idp.infrastructure.logging import get_logger
from idp.infrastructure.queue.redis import WorkerHeartbeat, clear_heartbeat, write_heartbeat

log = get_logger(__name__)


class PresencePublisher:
    def __init__(self, redis: Redis, beat: WorkerHeartbeat, *, interval_seconds: float) -> None:
        self._redis = redis
        self._beat = beat
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    @property
    def worker_id(self) -> str:
        return self._beat.worker_id

    async def beat_once(self) -> None:
        self._beat = replace(self._beat, last_seen_at=time.time())
        # TTL of three intervals tolerates a missed beat without flapping.
        await write_heartbeat(self._redis, self._beat, ttl_seconds=max(int(self._interval * 3), 3))

    async def _loop(self) -> None:
        while True:
            try:
                await self.beat_once()
            except Exception as exc:  # Redis blip: keep beating, never kill the worker
                log.warning("worker.heartbeat_failed", error=type(exc).__name__)
            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        await self.beat_once()
        self._task = asyncio.create_task(self._loop(), name="worker-presence")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        with contextlib.suppress(Exception):
            await clear_heartbeat(self._redis, self._beat.worker_id)
