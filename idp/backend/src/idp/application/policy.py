"""Effective processing policy per tenant.

The deployment configuration (`PROCESSING_MODE`, `ALLOW_MOCK_PROVIDERS`) is the
*ceiling*. Tenants keep an append-only, versioned policy in
`processing_policies` that can only be stricter: the effective policy is the
tenant's latest version clamped by the ceiling. Without a tenant policy the
ceiling applies (version 0). The router and the LLM gateway both consult it.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import AuditAction, AuditEntity, record_audit
from idp.config import Settings
from idp.domain.errors import AuthorizationError
from idp.domain.identity import Permission, Principal
from idp.domain.routing import PolicySnapshot, ProcessingMode, clamp_policy
from idp.infrastructure.db.models import ProcessingPolicy


class PolicyResolver(Protocol):
    async def resolve(self, session: AsyncSession, tenant_id: uuid.UUID) -> PolicySnapshot: ...


class StaticPolicyResolver:
    def __init__(self, policy: PolicySnapshot) -> None:
        self._policy = policy

    async def resolve(self, session: AsyncSession, tenant_id: uuid.UUID) -> PolicySnapshot:
        return self._policy


def default_policy(settings: Settings) -> PolicySnapshot:
    return PolicySnapshot(
        mode=ProcessingMode(settings.processing_mode),
        allow_mock_providers=settings.allow_mock_providers,
    )


def _snapshot(row: ProcessingPolicy) -> PolicySnapshot:
    return PolicySnapshot(
        mode=ProcessingMode(row.mode),
        allow_llm=row.allow_llm,
        allow_mock_providers=row.allow_mock_providers,
        max_cost_per_document=float(row.max_cost_per_document)
        if row.max_cost_per_document is not None
        else None,
        version=row.version,
    )


async def _latest(session: AsyncSession, tenant_id: uuid.UUID) -> ProcessingPolicy | None:
    return await session.scalar(
        select(ProcessingPolicy)
        .where(ProcessingPolicy.tenant_id == tenant_id)
        .order_by(ProcessingPolicy.version.desc())
        .limit(1)
    )


class DbPolicyResolver:
    def __init__(self, ceiling: PolicySnapshot) -> None:
        self._ceiling = ceiling

    async def resolve(self, session: AsyncSession, tenant_id: uuid.UUID) -> PolicySnapshot:
        row = await _latest(session, tenant_id)
        return self._ceiling if row is None else clamp_policy(_snapshot(row), self._ceiling)


class PolicyService:
    def __init__(self, session: AsyncSession, ceiling: PolicySnapshot) -> None:
        self._session = session
        self._ceiling = ceiling

    async def get(self, principal: Principal) -> tuple[PolicySnapshot, PolicySnapshot]:
        """(requested, effective) for the principal's tenant."""
        if not principal.has(Permission.CONFIG_READ):
            raise AuthorizationError("Missing permission 'config:read'")
        row = await _latest(self._session, principal.tenant_id)
        requested = self._ceiling if row is None else _snapshot(row)
        return requested, clamp_policy(requested, self._ceiling)

    @property
    def ceiling(self) -> PolicySnapshot:
        return self._ceiling

    async def update(
        self, principal: Principal, requested: PolicySnapshot
    ) -> tuple[PolicySnapshot, PolicySnapshot]:
        if not principal.has(Permission.TENANT_MANAGE):
            raise AuthorizationError("Missing permission 'tenant:manage'")
        current = await _latest(self._session, principal.tenant_id)
        version = (
            await self._session.scalar(
                select(func.coalesce(func.max(ProcessingPolicy.version), 0)).where(
                    ProcessingPolicy.tenant_id == principal.tenant_id
                )
            )
            or 0
        ) + 1
        row = ProcessingPolicy(
            tenant_id=principal.tenant_id,
            version=version,
            mode=requested.mode.value,
            allow_llm=requested.allow_llm,
            allow_mock_providers=requested.allow_mock_providers,
            max_cost_per_document=requested.max_cost_per_document,
            created_by_id=principal.user_id,
        )
        self._session.add(row)
        await self._session.flush()
        record_audit(
            self._session,
            action=AuditAction.POLICY_UPDATED,
            entity_type=AuditEntity.PROCESSING_POLICY,
            entity_id=row.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            before=_as_dict(_snapshot(current)) if current else None,
            after=_as_dict(_snapshot(row)),
        )
        await self._session.commit()
        snapshot = _snapshot(row)
        return snapshot, clamp_policy(snapshot, self._ceiling)


def _as_dict(p: PolicySnapshot) -> dict[str, object]:
    return {
        "mode": p.mode.value,
        "allow_llm": p.allow_llm,
        "allow_mock_providers": p.allow_mock_providers,
        "max_cost_per_document": p.max_cost_per_document,
        "version": p.version,
    }
