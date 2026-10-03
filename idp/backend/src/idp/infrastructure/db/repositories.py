"""Repositories. Tenant-owned lookups always require a tenant_id."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from idp.infrastructure.db.models import Tenant, User


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_live(self, tenant_id: uuid.UUID) -> Tenant | None:
        return await self._session.scalar(
            select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
        )

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return await self._session.scalar(
            select(Tenant).where(Tenant.slug == slug, Tenant.deleted_at.is_(None))
        )


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_for_login(self, email: str) -> User | None:
        """Global lookup by email — the only tenant-less user query (login)."""
        return await self._session.scalar(
            select(User).where(func.lower(User.email) == email.lower(), User.deleted_at.is_(None))
        )

    async def get(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
        return await self._session.scalar(
            select(User).where(
                User.id == user_id, User.tenant_id == tenant_id, User.deleted_at.is_(None)
            )
        )

    async def list(self, *, tenant_id: uuid.UUID) -> Sequence[User]:
        result = await self._session.scalars(
            select(User)
            .where(User.tenant_id == tenant_id, User.deleted_at.is_(None))
            .order_by(User.created_at)
        )
        return result.all()

    async def email_taken(self, email: str) -> bool:
        return await self.find_for_login(email) is not None
