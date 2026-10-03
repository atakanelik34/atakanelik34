"""Redis connectivity, worker heartbeats and queue health.

Postgres is the system of record; Redis carries only job IDs, rate-limit
counters and ephemeral worker presence. Losing Redis must never lose a document.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from arq.connections import ArqRedis, RedisSettings
from redis.asyncio import Redis

from idp.config import Settings
from idp.domain.health import ComponentHealth, HealthStatus

HEARTBEAT_KEY_PREFIX = "idp:workers:heartbeat:"
# arq's default queue name; phase 2 adds dedicated priority queues.
DEFAULT_QUEUE_NAME = "arq:queue"


def create_redis(settings: Settings) -> ArqRedis:
    """One client type for API and worker: plain Redis commands plus arq enqueueing."""
    client: ArqRedis = ArqRedis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=5,
        health_check_interval=30,
    )
    return client


def arq_redis_settings(settings: Settings) -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url.get_secret_value())


@dataclass(frozen=True, slots=True)
class WorkerHeartbeat:
    worker_id: str
    hostname: str
    pid: int
    version: str
    max_jobs: int
    started_at: float
    last_seen_at: float


async def write_heartbeat(redis: Redis, beat: WorkerHeartbeat, *, ttl_seconds: int) -> None:
    await redis.set(HEARTBEAT_KEY_PREFIX + beat.worker_id, json.dumps(asdict(beat)), ex=ttl_seconds)


async def clear_heartbeat(redis: Redis, worker_id: str) -> None:
    await redis.delete(HEARTBEAT_KEY_PREFIX + worker_id)


async def list_heartbeats(redis: Redis) -> list[WorkerHeartbeat]:
    beats: list[WorkerHeartbeat] = []
    async for key in redis.scan_iter(match=HEARTBEAT_KEY_PREFIX + "*", count=100):
        raw = await redis.get(key)
        if raw is None:  # expired between SCAN and GET
            continue
        beats.append(WorkerHeartbeat(**json.loads(raw)))
    return sorted(beats, key=lambda b: b.worker_id)


async def check_redis(redis: Redis) -> ComponentHealth:
    started = time.perf_counter()
    try:
        await redis.ping()
    except Exception as exc:
        return ComponentHealth(name="redis", status=HealthStatus.DOWN, detail=type(exc).__name__)
    return ComponentHealth(
        name="redis", status=HealthStatus.UP, latency_ms=(time.perf_counter() - started) * 1000
    )


async def check_workers(redis: Redis) -> ComponentHealth:
    try:
        beats = await list_heartbeats(redis)
        # arq scores jobs by run-at time (ms); count only jobs that are due, not
        # deferred retries or scheduled cron runs.
        queued = int(await redis.zcount(DEFAULT_QUEUE_NAME, "-inf", int(time.time() * 1000)))
    except Exception as exc:
        return ComponentHealth(name="workers", status=HealthStatus.DOWN, detail=type(exc).__name__)
    metadata = {
        "count": len(beats),
        "queued_jobs": queued,
        "workers": [
            {
                "worker_id": b.worker_id,
                "version": b.version,
                "max_jobs": b.max_jobs,
                "last_seen_at": b.last_seen_at,
                "started_at": b.started_at,
            }
            for b in beats
        ],
    }
    if not beats:
        # The API itself still works without workers; processing does not.
        return ComponentHealth(
            name="workers",
            status=HealthStatus.DEGRADED,
            detail="no worker heartbeat",
            metadata=metadata,
        )
    return ComponentHealth(name="workers", status=HealthStatus.UP, metadata=metadata)
