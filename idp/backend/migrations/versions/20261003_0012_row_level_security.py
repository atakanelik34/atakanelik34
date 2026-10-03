"""row level security

Tenant isolation enforced by Postgres for the runtime role: every table with a
`tenant_id` column (and `tenants` itself) gets a policy that admits only rows of
the tenant in `app.tenant_id`, or every row when it is `*` (system context).
An unset value admits nothing (fail closed). The table owner (migration role)
is not subject to RLS. Future tenant tables must add the same policy; a test
checks that none is missing.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-04 00:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = """
CREATE OR REPLACE FUNCTION idp_rls_allows(row_tenant uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT CASE coalesce(current_setting('app.tenant_id', true), '')
    WHEN '*' THEN true
    WHEN '' THEN false
    ELSE row_tenant = current_setting('app.tenant_id', true)::uuid
  END
$$;
"""

TENANT_TABLES = """
SELECT c.table_name FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_name = c.table_name AND t.table_schema = c.table_schema
WHERE c.column_name = 'tenant_id' AND c.table_schema = current_schema()
  AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name
"""


def upgrade() -> None:
    op.execute(FUNCTION)
    op.execute(
        f"""
        DO $$
        DECLARE t text;
        BEGIN
          FOR t IN {TENANT_TABLES} LOOP
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format(
              'CREATE POLICY tenant_isolation ON %I USING (idp_rls_allows(tenant_id)) '
              'WITH CHECK (idp_rls_allows(tenant_id))', t);
          END LOOP;
        END $$;
        """
    )
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenants USING (idp_rls_allows(id)) "
        "WITH CHECK (idp_rls_allows(id))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tenants")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DO $$
        DECLARE t text;
        BEGIN
          FOR t IN {TENANT_TABLES} LOOP
            EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
            EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', t);
          END LOOP;
        END $$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS idp_rls_allows(uuid)")
