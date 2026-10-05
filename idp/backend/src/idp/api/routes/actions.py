from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from idp.api.deps import ContainerDep, PrincipalDep, SessionDep, require
from idp.application.actions import ActionService
from idp.application.api_keys import ApiKeyService
from idp.domain.identity import Permission, Principal
from idp.infrastructure.db.models import ActionRun, ApiKey, OutboxEvent

router = APIRouter(tags=["actions"])

Executor = Annotated[Principal, Depends(require(Permission.ACTIONS_EXECUTE))]
AuditReader = Annotated[Principal, Depends(require(Permission.AUDIT_READ))]


def get_action_service(container: ContainerDep, session: SessionDep) -> ActionService:
    return ActionService(
        session, scheduler=container.scheduler, retry_budget=container.settings.job_max_attempts
    )


Actions = Annotated[ActionService, Depends(get_action_service)]


class ActionRunOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    part_id: uuid.UUID
    name: str
    connection_key: str
    kind: str
    status: str
    requires_approval: bool
    attempts: int
    payload: dict[str, Any]
    external_reference: str | None
    error_code: str | None
    is_mock: bool
    decided_by_id: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    executed_at: datetime | None
    deduplicated_from_id: uuid.UUID | None
    created_at: datetime


def _run(r: ActionRun) -> ActionRunOut:
    return ActionRunOut(
        id=r.id,
        job_id=r.job_id,
        part_id=r.part_id,
        name=r.name,
        connection_key=r.connection_key,
        kind=r.kind,
        status=r.status,
        requires_approval=r.requires_approval,
        attempts=r.attempts,
        payload=r.payload,
        external_reference=r.external_reference,
        error_code=r.error_code,
        is_mock=r.is_mock,
        decided_by_id=r.decided_by_id,
        decided_at=r.decided_at,
        decision_note=r.decision_note,
        executed_at=r.executed_at,
        deduplicated_from_id=r.deduplicated_from_id,
        created_at=r.created_at,
    )


class DecisionNote(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class DecisionReason(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.get("/documents/{document_id}/actions", response_model=list[ActionRunOut])
async def list_actions(
    document_id: uuid.UUID, principal: PrincipalDep, actions: Actions
) -> list[ActionRunOut]:
    return [_run(r) for r in await actions.list_runs(principal, document_id)]


@router.post("/documents/{document_id}/actions/approve", response_model=list[ActionRunOut])
async def approve_actions(
    document_id: uuid.UUID, body: DecisionNote, principal: Executor, actions: Actions
) -> list[ActionRunOut]:
    runs = await actions.decide(principal, document_id, approve=True, note=body.note)
    return [_run(r) for r in runs]


@router.post("/documents/{document_id}/actions/reject", response_model=list[ActionRunOut])
async def reject_actions(
    document_id: uuid.UUID, body: DecisionReason, principal: Executor, actions: Actions
) -> list[ActionRunOut]:
    runs = await actions.decide(principal, document_id, approve=False, note=body.reason)
    return [_run(r) for r in runs]


# --- API keys ----------------------------------------------------------------------


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_by_id: uuid.UUID
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["documents:read", "documents:write"])
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyCreated(ApiKeyOut):
    token: str  # shown once


def _key(k: ApiKey) -> dict[str, Any]:
    return {
        "id": k.id,
        "name": k.name,
        "prefix": k.prefix,
        "scopes": k.scopes,
        "created_by_id": k.created_by_id,
        "created_at": k.created_at,
        "last_used_at": k.last_used_at,
        "expires_at": k.expires_at,
        "revoked_at": k.revoked_at,
    }


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_keys(principal: PrincipalDep, session: SessionDep) -> list[ApiKeyOut]:
    return [ApiKeyOut(**_key(k)) for k in await ApiKeyService(session).list_keys(principal)]


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(
    body: ApiKeyCreate, principal: PrincipalDep, session: SessionDep
) -> ApiKeyCreated:
    key, token = await ApiKeyService(session).create(
        principal, name=body.name, scopes=body.scopes, expires_in_days=body.expires_in_days
    )
    return ApiKeyCreated(**_key(key), token=token)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> None:
    await ApiKeyService(session).revoke(principal, key_id)


# --- outbox events (delivery monitor) ------------------------------------------------


class EventOut(BaseModel):
    id: uuid.UUID
    event_type: str
    aggregate_id: uuid.UUID
    payload: dict[str, Any]
    created_at: datetime
    published_at: datetime | None
    attempts: int
    deliveries: dict[str, Any]
    last_error: str | None


@router.get("/events", response_model=list[EventOut])
async def list_events(
    principal: AuditReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[EventOut]:
    rows = (
        await session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.tenant_id == principal.tenant_id)
            .order_by(OutboxEvent.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        EventOut(
            id=e.id,
            event_type=e.event_type,
            aggregate_id=e.aggregate_id,
            payload=e.payload,
            created_at=e.created_at,
            published_at=e.published_at,
            attempts=e.attempts,
            deliveries=e.deliveries,
            last_error=e.last_error,
        )
        for e in rows
    ]
