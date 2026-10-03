"""Audit trail writer.

Audit rows are written in the caller's transaction so a state change and its
audit record commit (or roll back) together.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from idp.context import current_client_ip, current_correlation_id
from idp.domain.identity import Principal
from idp.infrastructure.db.models import AuditLog


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


class AuditAction(StrEnum):
    AUTH_LOGIN_SUCCEEDED = "auth.login_succeeded"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    USER_CREATED = "user.created"
    TENANT_BOOTSTRAPPED = "tenant.bootstrapped"


class AuditEntity(StrEnum):
    USER = "user"
    TENANT = "tenant"


def record_audit(
    session: AsyncSession,
    *,
    action: AuditAction,
    entity_type: AuditEntity,
    entity_id: uuid.UUID | str | None,
    tenant_id: uuid.UUID | None,
    actor: Principal | None = None,
    actor_type: ActorType | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    resolved_actor_type = actor_type or (ActorType.USER if actor else ActorType.ANONYMOUS)
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_type=resolved_actor_type.value,
        actor_id=actor.user_id if actor else None,
        action=action.value,
        entity_type=entity_type.value,
        entity_id=str(entity_id) if entity_id is not None else None,
        before=before,
        after=after,
        correlation_id=current_correlation_id(),
        ip_address=current_client_ip(),
    )
    session.add(entry)
    return entry
