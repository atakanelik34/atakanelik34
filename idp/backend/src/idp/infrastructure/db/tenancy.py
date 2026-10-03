"""Row-level security context (Postgres RLS, migration 0012).

Every transaction sets `app.tenant_id` (transaction-local) from the session:

* authenticated API requests bind the principal's tenant (`bind_tenant`), so
  the database itself refuses other tenants' rows even if a query forgot its
  `tenant_id` filter;
* everything else — unauthenticated endpoints (login), workers, the sweeper,
  the outbox relay, CLI — runs as `*` (system), which those trusted, ID-driven
  code paths need for cross-tenant work.

RLS binds only the runtime role (`idp_app`); the migration role owns the tables
and is not subject to it. See `idp provision-db-roles`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

SETTING = "app.tenant_id"
SYSTEM = "*"
_KEY = "rls_tenant"
_SET = text("SELECT set_config('app.tenant_id', :tenant, true)")


@event.listens_for(Session, "after_begin")
def _apply_tenant(session: Session, transaction: Any, connection: Any) -> None:
    connection.execute(_SET, {"tenant": session.info.get(_KEY, SYSTEM)})


async def bind_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Scope this session (current and later transactions) to one tenant."""
    session.info[_KEY] = str(tenant_id)
    if session.in_transaction():
        await session.execute(_SET, {"tenant": str(tenant_id)})
