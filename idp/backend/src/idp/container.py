"""Process-wide resources, created once per process and closed on shutdown.

This is the only place adapters are chosen. Tests build a Container with their
own settings to get fully wired, isolated instances.
"""

from __future__ import annotations

from dataclasses import dataclass

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from idp.application.health import HealthService
from idp.application.jobs import JobScheduler
from idp.config import Settings
from idp.infrastructure.db.session import check_database, create_engine, create_session_factory
from idp.infrastructure.queue.jobs import ArqJobQueue
from idp.infrastructure.queue.redis import check_redis, check_workers, create_redis
from idp.infrastructure.scanning import MalwareScanner, NoMalwareScanner
from idp.infrastructure.security.rate_limit import RedisRateLimiter
from idp.infrastructure.security.tokens import TokenService
from idp.infrastructure.storage.base import ObjectStorageProvider
from idp.infrastructure.storage.factory import create_storage
from idp.infrastructure.storage.s3 import S3ObjectStorage


@dataclass(slots=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: ArqRedis
    storage: ObjectStorageProvider
    scanner: MalwareScanner
    scheduler: JobScheduler
    tokens: TokenService
    login_limiter: RedisRateLimiter
    health: HealthService

    @classmethod
    def build(cls, settings: Settings) -> Container:
        engine = create_engine(settings)
        redis = create_redis(settings)
        storage = create_storage(settings)
        return cls(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
            redis=redis,
            storage=storage,
            scanner=NoMalwareScanner(),
            scheduler=JobScheduler(ArqJobQueue(redis), settings),
            tokens=TokenService(settings),
            login_limiter=RedisRateLimiter(
                redis,
                namespace="login",
                limit=settings.login_rate_limit_attempts,
                window_seconds=settings.login_rate_limit_window_seconds,
            ),
            health=HealthService(
                critical={
                    "database": lambda: check_database(engine),
                    "redis": lambda: check_redis(redis),
                    "storage": storage.check,
                },
                optional={"workers": lambda: check_workers(redis)},
            ),
        )

    async def startup(self) -> None:
        if self.settings.storage_auto_create_bucket and isinstance(self.storage, S3ObjectStorage):
            await self.storage.ensure_bucket()

    async def close(self) -> None:
        await self.redis.aclose()
        await self.engine.dispose()
