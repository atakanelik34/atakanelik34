"""Effective processing policy per tenant.

Phase 8: one deployment-wide default from configuration. Phase 9 adds a
versioned per-tenant `processing_policies` table behind the same resolver
port; the router and the LLM gateway both consult it.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from idp.config import Settings
from idp.domain.routing import PolicySnapshot, ProcessingMode


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
