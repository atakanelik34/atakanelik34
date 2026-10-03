"""Processing configuration and monitoring: providers, workflows, jobs."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from idp.api.deps import ContainerDep, SessionDep, require
from idp.application.policy import default_policy
from idp.application.workflows import DEFAULT_WORKFLOW, list_workflows
from idp.domain.identity import Permission, Principal
from idp.domain.lifecycle import JobStatus
from idp.domain.routing import DEFAULT_ROUTING_POLICY
from idp.infrastructure.db.models import Document, ProcessingJob
from idp.providers.catalog import describe_providers

router = APIRouter(tags=["processing"])

ConfigReader = Annotated[Principal, Depends(require(Permission.CONFIG_READ))]
DocumentReader = Annotated[Principal, Depends(require(Permission.DOCUMENTS_READ))]


class ProviderOut(BaseModel):
    kind: str
    name: str
    version: str
    method: str
    tier: int | None
    locality: str
    is_mock: bool
    status: str
    cost_per_page: float


class PolicyOut(BaseModel):
    mode: str
    allow_llm: bool
    allow_mock_providers: bool
    max_cost_per_document: float | None
    version: int
    routing_version: int


class ProvidersOut(BaseModel):
    providers: list[ProviderOut]
    policy: PolicyOut


class WorkflowOut(BaseModel):
    key: str
    version: int
    steps: list[str]
    final_status: str
    is_default: bool


class JobOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    workflow: str
    status: str
    trigger: str
    attempts: int
    max_attempts: int
    current_step: str | None
    last_error_code: str | None
    next_attempt_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@router.get("/providers", response_model=ProvidersOut)
async def providers(principal: ConfigReader, container: ContainerDep) -> ProvidersOut:
    del principal
    policy = default_policy(container.settings)
    return ProvidersOut(
        providers=[ProviderOut(**asdict(d)) for d in describe_providers(container.settings)],
        policy=PolicyOut(
            mode=policy.mode.value,
            allow_llm=policy.allow_llm,
            allow_mock_providers=policy.allow_mock_providers,
            max_cost_per_document=policy.max_cost_per_document,
            version=policy.version,
            routing_version=DEFAULT_ROUTING_POLICY.version,
        ),
    )


@router.get("/workflows", response_model=list[WorkflowOut])
async def workflows(principal: ConfigReader) -> list[WorkflowOut]:
    del principal
    return [
        WorkflowOut(
            key=w.key,
            version=w.version,
            steps=list(w.steps),
            final_status=w.final_status.value,
            is_default=w == DEFAULT_WORKFLOW,
        )
        for w in list_workflows()
    ]


@router.get("/jobs", response_model=list[JobOut])
async def jobs(
    principal: DocumentReader,
    session: SessionDep,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobOut]:
    query = (
        select(ProcessingJob, Document.original_filename)
        .join(Document, Document.id == ProcessingJob.document_id)
        .where(ProcessingJob.tenant_id == principal.tenant_id)
        .order_by(ProcessingJob.created_at.desc())
        .limit(limit)
    )
    if status:
        query = query.where(ProcessingJob.status.in_(status))
    rows = (await session.execute(query)).all()
    return [
        JobOut(
            id=job.id,
            document_id=job.document_id,
            document_name=name,
            workflow=f"{job.workflow_key}@v{job.workflow_version}",
            status=job.status.value,
            trigger=job.trigger,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            current_step=job.current_step,
            last_error_code=job.last_error_code,
            next_attempt_at=job.next_attempt_at,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
        for job, name in rows
    ]
