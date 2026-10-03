"""Postgres row-level security: policies exist everywhere and isolate tenants."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from idp.container import Container
from idp.infrastructure.db.roles import grant_statements
from idp.infrastructure.db.tenancy import bind_tenant
from tests.integration.conftest import TenantFixture, bootstrap_tenant


async def test_every_tenant_table_has_row_level_security(container: Container) -> None:
    async with container.session_factory() as session:
        missing = (
            (
                await session.execute(
                    text(
                        """
                    SELECT c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    JOIN information_schema.columns col
                      ON col.table_name = c.relname AND col.table_schema = n.nspname
                    WHERE n.nspname = current_schema() AND c.relkind = 'r'
                      AND col.column_name = 'tenant_id' AND NOT c.relrowsecurity
                    """
                    )
                )
            )
            .scalars()
            .all()
        )
    assert missing == []


async def _visible_users(session, tenant: str) -> list[uuid.UUID]:  # type: ignore[no-untyped-def]
    await session.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
    return list((await session.execute(text("SELECT DISTINCT tenant_id FROM users"))).scalars())


async def test_policies_isolate_tenants_for_non_owner_roles(
    container: Container, acme: TenantFixture
) -> None:
    globex = await bootstrap_tenant(container, "globex")
    async with container.session_factory() as session:
        acme_id = await session.scalar(text("SELECT id FROM tenants WHERE slug = 'acme'"))
        globex_id = await session.scalar(
            text("SELECT id FROM tenants WHERE slug = :s"), {"s": globex.slug}
        )
        # The test user owns the tables; FORCE applies the policies to it inside this
        # transaction only (rolled back below), as they apply to the runtime role.
        await session.execute(text("ALTER TABLE users FORCE ROW LEVEL SECURITY"))
        await session.execute(text("ALTER TABLE tenants FORCE ROW LEVEL SECURITY"))
        assert await _visible_users(session, str(acme_id)) == [acme_id]
        assert set(await _visible_users(session, "*")) == {acme_id, globex_id}
        assert await _visible_users(session, "") == []  # unset: fail closed
        tenants = (await session.execute(text("SELECT slug FROM tenants"))).scalars().all()
        assert tenants == []
        await bind_tenant(session, acme_id)
        with pytest.raises(DBAPIError, match="row-level security"):
            # Moving a row to another tenant fails the WITH CHECK clause.
            await session.execute(
                text("UPDATE users SET tenant_id = :g WHERE tenant_id = :a"),
                {"g": globex_id, "a": acme_id},
            )
        await session.rollback()


async def test_bound_tenant_survives_commits(container: Container, acme: TenantFixture) -> None:
    tenant = uuid.uuid4()
    async with container.session_factory() as session:
        assert await session.scalar(text("SELECT current_setting('app.tenant_id', true)")) == "*"
        await bind_tenant(session, tenant)
        assert await session.scalar(text("SELECT current_setting('app.tenant_id', true)")) == str(
            tenant
        )
        await session.commit()
        assert await session.scalar(text("SELECT current_setting('app.tenant_id', true)")) == str(
            tenant
        )
    async with container.session_factory() as fresh:
        assert await fresh.scalar(text("SELECT current_setting('app.tenant_id', true)")) == "*"


def test_runtime_role_grants_keep_audit_append_only() -> None:
    grants = grant_statements("idp_app", "idp", "idp")
    assert 'REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM "idp_app"' in grants
    assert not any("SUPERUSER" in g or "BYPASSRLS" in g for g in grants)
