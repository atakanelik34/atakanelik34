"""Async engine and session factory."""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import idp.infrastructure.db.tenancy  # noqa: F401 — registers the RLS session hook
from idp.config import DatabaseSettings, Settings
from idp.domain.health import ComponentHealth, HealthStatus


def create_engine(settings: Settings | DatabaseSettings) -> AsyncEngine:
    pool: dict[str, int] = (
        {"pool_size": settings.database_pool_size, "max_overflow": settings.database_max_overflow}
        if isinstance(settings, Settings)
        else {}
    )
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        echo=settings.database_echo,
        **pool,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def check_database(engine: AsyncEngine) -> ComponentHealth:
    started = time.perf_counter()
    try:
        async with engine.connect() as conn:
            revision = (
                await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            ).scalar_one_or_none()
    except Exception as exc:
        return ComponentHealth(name="database", status=HealthStatus.DOWN, detail=type(exc).__name__)
    latency = (time.perf_counter() - started) * 1000
    if revision is None:
        return ComponentHealth(
            name="database",
            status=HealthStatus.DEGRADED,
            latency_ms=latency,
            detail="migrations not applied",
        )
    return ComponentHealth(
        name="database",
        status=HealthStatus.UP,
        latency_ms=latency,
        metadata={"schema_revision": revision},
    )
