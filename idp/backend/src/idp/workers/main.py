"""Worker composition root (arq).

Run with:  arq idp.workers.main.WorkerSettings

Workers are stateless and horizontally scalable: every job loads its state from
Postgres by ID. Phase 1 registers only the presence heartbeat; pipeline steps
are registered here as phases land.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from dataclasses import replace
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis

from idp.config import get_settings
from idp.infrastructure.logging import configure_logging, get_logger
from idp.infrastructure.queue.redis import (
    WorkerHeartbeat,
    arq_redis_settings,
    clear_heartbeat,
    create_redis,
    write_heartbeat,
)

log = get_logger(__name__)

_settings = get_settings()
_HEARTBEAT_SECONDS = set(range(0, 60, max(1, min(_settings.worker_heartbeat_seconds, 59))))


async def heartbeat(ctx: dict[str, Any]) -> None:
    beat = replace(ctx["heartbeat"], last_seen_at=time.time())
    ctx["heartbeat"] = beat
    redis: Redis = ctx["presence_redis"]
    await write_heartbeat(redis, beat, ttl_seconds=_settings.worker_heartbeat_seconds * 3)


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(_settings.log_level, _settings.log_format)
    # arq's CLI installs its own plain-text handler; route through ours only.
    logging.getLogger("arq").handlers.clear()
    hostname = socket.gethostname()
    now = time.time()
    ctx["presence_redis"] = create_redis(_settings)
    ctx["heartbeat"] = WorkerHeartbeat(
        worker_id=f"{hostname}:{os.getpid()}",
        hostname=hostname,
        pid=os.getpid(),
        version=_settings.pipeline_version,
        max_jobs=_settings.worker_max_jobs,
        started_at=now,
        last_seen_at=now,
    )
    await heartbeat(ctx)
    log.info("worker.started", worker_id=ctx["heartbeat"].worker_id)


async def shutdown(ctx: dict[str, Any]) -> None:
    redis: Redis = ctx["presence_redis"]
    await clear_heartbeat(redis, ctx["heartbeat"].worker_id)
    await redis.aclose()
    log.info("worker.stopped", worker_id=ctx["heartbeat"].worker_id)


class WorkerSettings:
    functions: ClassVar[list[Any]] = []
    # unique=False: every worker writes its own presence, not one per cluster.
    cron_jobs: ClassVar[list[Any]] = [
        cron(heartbeat, second=_HEARTBEAT_SECONDS, unique=False, run_at_startup=False)
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings: RedisSettings = arq_redis_settings(_settings)
    max_jobs = _settings.worker_max_jobs
    job_timeout = 600
    keep_result = 3600
    health_check_interval = 30
