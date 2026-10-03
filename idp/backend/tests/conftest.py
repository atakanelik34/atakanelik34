"""Shared fixtures.

Unit tests need nothing external. Integration/API tests need PostgreSQL and
Redis, given by TEST_DATABASE_URL and TEST_REDIS_URL; they are skipped (with a
visible reason) when those are absent. CI always sets them.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

# Settings validation requires a strong JWT secret before anything imports config.
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))

from sqlalchemy import text

from idp.config import Environment, LogFormat, Settings, StorageBackend
from idp.container import Container

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL")


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.TEST,
        "log_format": LogFormat.CONSOLE,
        "log_level": "WARNING",
        "jwt_secret": secrets.token_urlsafe(48),
        "storage_backend": StorageBackend.LOCAL,
        "storage_local_root": str(tmp_path / "storage"),
        "database_url": TEST_DATABASE_URL or "postgresql+asyncpg://unused@localhost/unused",
        "redis_url": TEST_REDIS_URL or "redis://localhost:6379/15",
        "login_rate_limit_attempts": 5,
        **overrides,
    }
    return Settings.model_validate(values)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


INTEGRATION_DIR = Path(__file__).resolve().parent / "integration"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL and TEST_REDIS_URL not set")
    for item in items:
        if item.path.is_relative_to(INTEGRATION_DIR):
            item.add_marker(pytest.mark.integration)
            if not (TEST_DATABASE_URL and TEST_REDIS_URL):
                item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated_database() -> str:
    assert TEST_DATABASE_URL is not None
    # No JWT_SECRET: migrations must only need the database URL.
    env = {k: v for k, v in os.environ.items() if k != "JWT_SECRET"}
    env["DATABASE_URL"] = TEST_DATABASE_URL
    for args in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(  # noqa: S603 — fixed argv, test-only
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_ROOT,
            env=env,
            check=True,
            capture_output=True,
        )
    return TEST_DATABASE_URL


@pytest.fixture
async def container(migrated_database: str, settings: Settings) -> AsyncIterator[Container]:
    c = Container.build(settings)
    async with c.engine.begin() as conn:
        await conn.execute(text("TRUNCATE tenants, users, projects, audit_logs CASCADE"))
    await c.redis.flushdb()
    await c.startup()
    yield c
    await c.redis.flushdb()
    await c.close()
