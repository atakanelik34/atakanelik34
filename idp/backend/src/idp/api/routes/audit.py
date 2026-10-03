from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, IPvAnyAddress
from sqlalchemy import and_, or_, select

from idp.api.deps import SessionDep, require
from idp.application.documents import decode_cursor, encode_cursor
from idp.domain.identity import Permission, Principal
from idp.infrastructure.db.models import AuditLog, User

router = APIRouter(prefix="/audit-logs", tags=["audit"])


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    actor_type: str
    actor_id: uuid.UUID | None
    actor_email: str | None = None
    action: str
    entity_type: str
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    correlation_id: str | None
    ip_address: IPvAnyAddress | None


class AuditPage(BaseModel):
    items: list[AuditLogOut]
    next_cursor: str | None


@router.get("", response_model=AuditPage)
async def list_audit_logs(
    principal: Annotated[Principal, Depends(require(Permission.AUDIT_READ))],
    session: SessionDep,
    action: Annotated[str | None, Query(max_length=100)] = None,
    entity_type: Annotated[str | None, Query(max_length=64)] = None,
    entity_id: Annotated[str | None, Query(max_length=64)] = None,
    actor_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> AuditPage:
    """Append-only audit trail for the caller's tenant, newest first."""
    query = (
        select(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(AuditLog.tenant_id == principal.tenant_id)
    )
    if action:
        query = query.where(AuditLog.action.startswith(action))
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)
    if cursor:
        at, last_id = decode_cursor(cursor)
        query = query.where(
            or_(AuditLog.occurred_at < at, and_(AuditLog.occurred_at == at, AuditLog.id < last_id))
        )
    rows = (
        await session.execute(
            query.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(limit + 1)
        )
    ).all()
    items = []
    for log, email in rows[:limit]:
        item = AuditLogOut.model_validate(log)
        item.actor_email = email
        items.append(item)
    last = rows[limit - 1][0] if len(rows) > limit else None
    return AuditPage(
        items=items, next_cursor=encode_cursor(last.occurred_at, last.id) if last else None
    )
