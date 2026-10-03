"""Processing configuration and monitoring: providers, workflows, jobs."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from idp.api.deps import ContainerDep, SessionDep, require
from idp.application.policy import PolicyService, default_policy
from idp.application.workflows import DEFAULT_WORKFLOW, list_workflows
from idp.domain.identity import Permission, Principal
from idp.domain.lifecycle import JobStatus
from idp.domain.routing import DEFAULT_ROUTING_POLICY, PolicySnapshot, ProcessingMode
from idp.infrastructure.db.models import Document, ProcessingJob, ProviderCall
from idp.providers.catalog import describe_providers

router = APIRouter(tags=["processing"])

ConfigReader = Annotated[Principal, Depends(require(Permission.CONFIG_READ))]
DocumentReader = Annotated[Principal, Depends(require(Permission.DOCUMENTS_READ))]
TenantManager = Annotated[Principal, Depends(require(Permission.TENANT_MANAGE))]


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


def _policy_out(policy: PolicySnapshot) -> PolicyOut:
    return PolicyOut(
        mode=policy.mode.value,
        allow_llm=policy.allow_llm,
        allow_mock_providers=policy.allow_mock_providers,
        max_cost_per_document=policy.max_cost_per_document,
        version=policy.version,
        routing_version=DEFAULT_ROUTING_POLICY.version,
    )


def get_policy_service(container: ContainerDep, session: SessionDep) -> PolicyService:
    return PolicyService(session, default_policy(container.settings))


Policies = Annotated[PolicyService, Depends(get_policy_service)]


@router.get("/providers", response_model=ProvidersOut)
async def providers(
    principal: ConfigReader, container: ContainerDep, policies: Policies
) -> ProvidersOut:
    _, effective = await policies.get(principal)
    return ProvidersOut(
        providers=[ProviderOut(**asdict(d)) for d in await describe_providers(container.settings)],
        policy=_policy_out(effective),
    )


class PolicyStateOut(BaseModel):
    requested: PolicyOut
    effective: PolicyOut
    ceiling: PolicyOut


class PolicyUpdate(BaseModel):
    mode: ProcessingMode
    allow_llm: bool = True
    allow_mock_providers: bool = False
    max_cost_per_document: float | None = Field(default=None, ge=0, le=1000)


@router.get("/processing-policy", response_model=PolicyStateOut)
async def get_policy(principal: ConfigReader, policies: Policies) -> PolicyStateOut:
    requested, effective = await policies.get(principal)
    return PolicyStateOut(
        requested=_policy_out(requested),
        effective=_policy_out(effective),
        ceiling=_policy_out(policies.ceiling),
    )


@router.put("/processing-policy", response_model=PolicyStateOut)
async def update_policy(
    body: PolicyUpdate, principal: TenantManager, policies: Policies
) -> PolicyStateOut:
    requested, effective = await policies.update(
        principal,
        PolicySnapshot(
            mode=body.mode,
            allow_llm=body.allow_llm,
            allow_mock_providers=body.allow_mock_providers,
            max_cost_per_document=body.max_cost_per_document,
        ),
    )
    return PolicyStateOut(
        requested=_policy_out(requested),
        effective=_policy_out(effective),
        ceiling=_policy_out(policies.ceiling),
    )


class UsageRow(BaseModel):
    provider: str
    model: str
    locality: str
    purpose: str
    status: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost: float


@router.get("/providers/usage", response_model=list[UsageRow])
async def provider_usage(
    principal: ConfigReader,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
) -> list[UsageRow]:
    since = datetime.now(UTC) - timedelta(days=days)
    keys = (
        ProviderCall.provider,
        ProviderCall.model,
        ProviderCall.locality,
        ProviderCall.purpose,
        ProviderCall.status,
    )
    rows = await session.execute(
        select(
            *keys,
            func.count(),
            func.coalesce(func.sum(ProviderCall.input_tokens), 0),
            func.coalesce(func.sum(ProviderCall.output_tokens), 0),
            func.coalesce(func.sum(ProviderCall.cost), 0),
        )
        .where(ProviderCall.tenant_id == principal.tenant_id, ProviderCall.created_at >= since)
        .group_by(*keys)
        .order_by(ProviderCall.provider, ProviderCall.purpose)
    )
    return [
        UsageRow(
            provider=provider,
            model=model,
            locality=locality,
            purpose=purpose,
            status=status_,
            calls=calls,
            input_tokens=int(tin),
            output_tokens=int(tout),
            cost=float(cost),
        )
        for provider, model, locality, purpose, status_, calls, tin, tout, cost in rows
    ]


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
