"""Job queue port and its arq adapter.

The queue carries only job ids. Messages may be lost or duplicated; Postgres
state plus the lease-based claim make that harmless (ARCHITECTURE.md §7).
"""

from __future__ import annotations

import uuid
from typing import Protocol

from arq.connections import ArqRedis

PROCESS_JOB_FUNCTION = "process_job"


class JobQueue(Protocol):
    async def enqueue(self, job_id: uuid.UUID, *, token: int, defer_seconds: float = 0) -> None:
        """Request execution of `job_id`.

        `token` (the job's attempt counter) makes the message id deterministic, so
        re-enqueueing the same job state is de-duplicated by the broker.
        """
        ...


def message_id(job_id: uuid.UUID, token: int) -> str:
    return f"idp-job:{job_id}:{token}"


class ArqJobQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue(self, job_id: uuid.UUID, *, token: int, defer_seconds: float = 0) -> None:
        await self._redis.enqueue_job(
            PROCESS_JOB_FUNCTION,
            str(job_id),
            _job_id=message_id(job_id, token),
            _defer_by=defer_seconds if defer_seconds > 0 else None,
        )
