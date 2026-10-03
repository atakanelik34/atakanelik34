"""Tenant operations overview for the dashboard (read-only aggregates)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idp.domain.identity import Principal
from idp.domain.lifecycle import DocumentStatus, JobStatus, StepStatus
from idp.infrastructure.db.models import (
    ActionRun,
    Document,
    ProcessingJob,
    ProcessingStep,
    ProviderCall,
    ReviewTask,
)

WINDOW_DAYS = 30


async def overview(session: AsyncSession, principal: Principal) -> dict[str, Any]:
    tenant = principal.tenant_id
    since = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)
    by_status = dict(
        (
            await session.execute(
                select(Document.status, func.count())
                .where(Document.tenant_id == tenant, Document.deleted_at.is_(None))
                .group_by(Document.status)
            )
        ).all()
    )
    jobs = dict(
        (
            await session.execute(
                select(ProcessingJob.status, func.count())
                .where(ProcessingJob.tenant_id == tenant, ProcessingJob.created_at >= since)
                .group_by(ProcessingJob.status)
            )
        ).all()
    )
    succeeded_jobs = int(jobs.get(JobStatus.SUCCEEDED, 0))
    # Straight-through: finished jobs that never needed a human review.
    untouched = (
        await session.scalar(
            select(func.count()).where(
                ProcessingJob.tenant_id == tenant,
                ProcessingJob.status == JobStatus.SUCCEEDED,
                ProcessingJob.created_at >= since,
                ~select(ReviewTask.id).where(ReviewTask.job_id == ProcessingJob.id).exists(),
            )
        )
        or 0
    )
    open_reviews = (
        await session.scalar(
            select(func.count()).where(
                ReviewTask.tenant_id == tenant, ReviewTask.status.in_(("open", "in_progress"))
            )
        )
        or 0
    )
    # Machine time per finished job, excluding time spent waiting for people.
    machine_ms = (
        select(func.sum(ProcessingStep.duration_ms).label("ms"))
        .join(ProcessingJob, ProcessingJob.id == ProcessingStep.job_id)
        .where(
            ProcessingJob.tenant_id == tenant,
            ProcessingJob.status == JobStatus.SUCCEEDED,
            ProcessingJob.created_at >= since,
            ProcessingStep.status == StepStatus.SUCCEEDED,
            ProcessingStep.step_key.not_in(("review", "approve_actions")),
        )
        .group_by(ProcessingStep.job_id)
        .subquery()
    )
    avg_ms = await session.scalar(select(func.avg(machine_ms.c.ms)))
    llm = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(ProviderCall.cost), 0),
                func.coalesce(func.sum(ProviderCall.input_tokens + ProviderCall.output_tokens), 0),
            ).where(ProviderCall.tenant_id == tenant, ProviderCall.created_at >= since)
        )
    ).one()
    actions = dict(
        (
            await session.execute(
                select(ActionRun.status, func.count())
                .where(ActionRun.tenant_id == tenant, ActionRun.created_at >= since)
                .group_by(ActionRun.status)
            )
        ).all()
    )
    return {
        "window_days": WINDOW_DAYS,
        "documents": {s.value: int(by_status.get(s, 0)) for s in DocumentStatus},
        "jobs": {s.value: int(jobs.get(s, 0)) for s in JobStatus},
        "open_reviews": int(open_reviews),
        "straight_through_rate": round(untouched / succeeded_jobs, 4) if succeeded_jobs else None,
        "avg_processing_ms": int(avg_ms) if avg_ms is not None else None,
        "llm": {"calls": int(llm[0]), "cost": float(llm[1]), "tokens": int(llm[2])},
        "actions": {k: int(v) for k, v in actions.items()},
    }
