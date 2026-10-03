from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from pydantic import BaseModel, Field

from idp.api.deps import ContainerDep, SessionDep, require
from idp.application.connections import ConnectionService, ConnectionSummary
from idp.domain.errors import PayloadTooLargeError
from idp.domain.identity import Permission, Principal
from idp.providers.enrichment.factory import (
    close_enrichment_providers,
    create_enrichment_providers,
)

router = APIRouter(prefix="/connections", tags=["connections"])

Reader = Annotated[Principal, Depends(require(Permission.CONFIG_READ))]
Writer = Annotated[Principal, Depends(require(Permission.CONFIG_WRITE))]


def get_service(container: ContainerDep, session: SessionDep) -> ConnectionService:
    return ConnectionService(session, container.settings)


Service = Annotated[ConnectionService, Depends(get_service)]

KIND_PATTERN = "^(master_data|rest|mock_erp|webhook|email)$"


class ConnectionOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    kind: str
    config: dict[str, Any]
    is_active: bool
    is_mock: bool
    records: int
    created_at: datetime


class ConnectionCreate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern=KIND_PATTERN)
    config: dict[str, Any] = Field(default_factory=dict)


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict[str, Any] | None = None
    is_active: bool | None = None


class ImportOut(BaseModel):
    imported: int
    skipped: int
    errors: list[str]


class RecordOut(BaseModel):
    key: str
    entity: str
    name: str
    attributes: dict[str, Any]


class RecordPage(BaseModel):
    items: list[RecordOut]
    total: int


class LookupRequest(BaseModel):
    entity: str = Field(default="vendor", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    criteria: dict[str, str] = Field(min_length=1, max_length=10)
    min_score: float = Field(default=0.85, ge=0.5, le=1)


class LookupOut(BaseModel):
    status: str
    best: dict[str, Any] | None
    candidates: list[dict[str, Any]]


def _out(summary: ConnectionSummary) -> ConnectionOut:
    c = summary.connection
    return ConnectionOut(
        id=c.id,
        key=c.key,
        name=c.name,
        kind=c.kind,
        config=c.config,
        is_active=c.is_active,
        is_mock=c.kind == "mock_erp",
        records=summary.records,
        created_at=c.created_at,
    )


@router.get("", response_model=list[ConnectionOut])
async def list_connections(principal: Reader, service: Service) -> list[ConnectionOut]:
    return [_out(s) for s in await service.list_connections(principal)]


@router.post("", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: ConnectionCreate, principal: Writer, service: Service
) -> ConnectionOut:
    return _out(
        await service.create(
            principal, key=body.key, name=body.name, kind=body.kind, config=body.config
        )
    )


@router.get("/{connection_id}", response_model=ConnectionOut)
async def get_connection(
    connection_id: uuid.UUID, principal: Reader, service: Service
) -> ConnectionOut:
    return _out(await service.get(principal, connection_id))


@router.patch("/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: uuid.UUID, body: ConnectionUpdate, principal: Writer, service: Service
) -> ConnectionOut:
    return _out(
        await service.update(
            principal,
            connection_id,
            name=body.name,
            config=body.config,
            is_active=body.is_active,
        )
    )


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(connection_id: uuid.UUID, principal: Writer, service: Service) -> None:
    await service.delete(principal, connection_id)


@router.post("/{connection_id}/records/import", response_model=ImportOut)
async def import_records(
    connection_id: uuid.UUID,
    principal: Writer,
    service: Service,
    container: ContainerDep,
    file: Annotated[UploadFile, File()],
    entity: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{0,62}$")] = "vendor",
) -> ImportOut:
    limit = container.settings.master_data_max_import_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLargeError("The CSV file is too large")
    report = await service.import_csv(principal, connection_id, entity=entity, data=data)
    return ImportOut(imported=report.imported, skipped=report.skipped, errors=report.errors)


@router.get("/{connection_id}/records", response_model=RecordPage)
async def list_records(
    connection_id: uuid.UUID,
    principal: Reader,
    service: Service,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RecordPage:
    rows, total = await service.records(
        principal, connection_id, query=q, limit=limit, offset=offset
    )
    return RecordPage(
        items=[
            RecordOut(key=r.record_key, entity=r.entity, name=r.name, attributes=r.attributes)
            for r in rows
        ],
        total=total,
    )


@router.post("/{connection_id}/test", response_model=LookupOut)
async def test_lookup(
    connection_id: uuid.UUID,
    body: LookupRequest,
    principal: Writer,
    service: Service,
    container: ContainerDep,
) -> LookupOut:
    providers = create_enrichment_providers(container.settings)
    try:
        decision = await service.test_lookup(
            principal,
            connection_id,
            entity=body.entity,
            criteria=body.criteria,
            providers=providers,
            min_score=body.min_score,
        )
    finally:
        await close_enrichment_providers(providers)

    def view(m: Any) -> dict[str, Any]:
        return {
            "key": m.record.key,
            "name": m.record.name,
            "attributes": dict(m.record.attributes),
            "score": round(m.score, 4),
            "matched_on": list(m.matched_on),
        }

    return LookupOut(
        status=decision.status,
        best=view(decision.best) if decision.best else None,
        candidates=[view(m) for m in decision.candidates],
    )
