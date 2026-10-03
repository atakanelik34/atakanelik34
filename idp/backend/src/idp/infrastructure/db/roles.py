"""Provision the runtime database role (run as the migration/owner role).

The runtime role can read and write application tables but owns nothing, is
not a superuser and cannot bypass RLS — so tenant isolation is enforced by the
database for the API and workers. `audit_logs` is append-only at the privilege
level too (no UPDATE/DELETE), on top of its trigger.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MIN_PASSWORD_LENGTH = 16


def grant_statements(role: str, owner: str, database: str) -> list[str]:
    q = f'"{role}"'
    return [
        f'GRANT CONNECT ON DATABASE "{database}" TO {q}',
        f"GRANT USAGE ON SCHEMA public TO {q}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {q}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {q}",
        f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM {q}",
        f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM {q}",
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public '
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {q}",
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public '
        f"GRANT USAGE, SELECT ON SEQUENCES TO {q}",
    ]


async def provision_app_role(conn: AsyncConnection, role: str, password: str) -> str:
    if not ROLE_NAME.match(role):
        raise ValueError("invalid role name")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"the role password must be at least {MIN_PASSWORD_LENGTH} characters")
    owner = await conn.scalar(text("SELECT current_user"))
    database = await conn.scalar(text("SELECT current_database()"))
    if owner == role:
        raise ValueError("the runtime role must differ from the migration role")
    exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
    verb = "ALTER" if exists else "CREATE"
    # Quote server-side so neither name nor password is ever spliced in Python.
    statement = await conn.scalar(
        text(
            "SELECT format('%s ROLE %I WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
            "NOCREATEROLE NOINHERIT PASSWORD %L', CAST(:verb AS text), "
            "CAST(:role AS text), CAST(:password AS text))"
        ),
        {"verb": verb, "role": role, "password": password},
    )
    try:
        await conn.exec_driver_sql(str(statement))
    except Exception:  # the statement embeds the password: never let it reach a log
        raise RuntimeError(f"could not {verb.lower()} role '{role}'") from None
    for grant in grant_statements(role, str(owner), str(database)):
        await conn.exec_driver_sql(grant)
    return "updated" if exists else "created"
