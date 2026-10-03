"""Operational CLI.

    idp bootstrap --tenant-slug acme --tenant-name "Acme Ltd" \
                  --email admin@acme.test --name "Acme Admin"

The owner password is read from IDP_BOOTSTRAP_PASSWORD or prompted for; it is
never accepted as a command-line argument (it would land in shell history).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from idp.application.users import TenantBootstrapService
from idp.config import get_database_settings
from idp.domain.errors import IDPError
from idp.infrastructure.db.roles import provision_app_role
from idp.infrastructure.db.session import create_engine, create_session_factory

PASSWORD_ENV = "IDP_BOOTSTRAP_PASSWORD"  # noqa: S105 — env var name, not a value
APP_ROLE_PASSWORD_ENV = "POSTGRES_APP_PASSWORD"  # noqa: S105 — env var name, not a value


async def _provision(args: argparse.Namespace) -> int:
    password = os.environ.get(APP_ROLE_PASSWORD_ENV, "")
    engine = create_engine(get_database_settings())
    try:
        async with engine.begin() as conn:
            outcome = await provision_app_role(conn, args.role, password)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(f"runtime role '{args.role}' {outcome}")
    return 0


async def _bootstrap(args: argparse.Namespace, password: str) -> int:
    engine = create_engine(get_database_settings())
    try:
        async with create_session_factory(engine)() as session:
            result = await TenantBootstrapService(session).bootstrap(
                tenant_slug=args.tenant_slug,
                tenant_name=args.tenant_name,
                owner_email=args.email,
                owner_name=args.name,
                password=password,
            )
    except IDPError as exc:
        if args.if_missing and exc.code == "conflict":
            print(f"skipped: {exc.message}")
            return 0
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(f"tenant '{result.tenant.slug}' created ({result.tenant.id}); owner {result.owner.email}")
    return 0


def _read_password() -> str:
    password = os.environ.get(PASSWORD_ENV)
    if password:
        return password
    if not sys.stdin.isatty():
        print(f"error: set {PASSWORD_ENV} when running non-interactively", file=sys.stderr)
        raise SystemExit(2)
    first = getpass.getpass("Owner password: ")
    if first != getpass.getpass("Repeat password: "):
        print("error: passwords do not match", file=sys.stderr)
        raise SystemExit(2)
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="idp")
    sub = parser.add_subparsers(dest="command", required=True)
    boot = sub.add_parser("bootstrap", help="create a tenant, its owner and default project")
    boot.add_argument("--tenant-slug", required=True)
    boot.add_argument("--tenant-name", required=True)
    boot.add_argument("--email", required=True)
    boot.add_argument("--name", required=True, help="owner full name")
    boot.add_argument(
        "--if-missing", action="store_true", help="exit 0 if the tenant or user already exists"
    )
    roles = sub.add_parser(
        "provision-db-roles",
        help=f"create/update the runtime DB role (password from {APP_ROLE_PASSWORD_ENV})",
    )
    roles.add_argument("--role", default="idp_app")
    args = parser.parse_args(argv)

    if args.command == "bootstrap":
        return asyncio.run(_bootstrap(args, _read_password()))
    if args.command == "provision-db-roles":
        return asyncio.run(_provision(args))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
