"""API composition root.

Run with:  uvicorn idp.main:create_app --factory
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from idp.api.errors import register_error_handlers
from idp.api.middleware import CORRELATION_HEADER, RequestContextMiddleware
from idp.api.routes import auth, health, system, users
from idp.api.routes import storage as storage_routes
from idp.config import Settings, StorageBackend, get_settings
from idp.container import Container
from idp.infrastructure.logging import configure_logging, get_logger

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)
    container = Container.build(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await container.startup()
        log.info("api.started", environment=settings.environment.value)
        try:
            yield
        finally:
            await container.close()
            log.info("api.stopped")

    docs = settings.api_docs_enabled
    app = FastAPI(
        title="IDP Platform API",
        version=settings.pipeline_version,
        lifespan=lifespan,
        docs_url=f"{settings.api_prefix}/docs" if docs else None,
        redoc_url=None,
        openapi_url=f"{settings.api_prefix}/openapi.json" if docs else None,
    )
    app.state.container = container

    register_error_handlers(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", CORRELATION_HEADER],
            expose_headers=[CORRELATION_HEADER],
        )
    app.add_middleware(
        RequestContextMiddleware, api_prefix=settings.api_prefix, hsts=settings.is_production
    )

    api = APIRouter(prefix=settings.api_prefix)
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(users.router)
    api.include_router(system.router)
    if settings.storage_backend is StorageBackend.LOCAL:
        api.include_router(storage_routes.router)
    app.include_router(api)
    return app
