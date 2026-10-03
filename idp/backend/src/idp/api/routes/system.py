from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from idp.api.deps import ContainerDep, SessionDep, require
from idp.api.schemas.system import ComponentStatusOut, SystemStatusResponse
from idp.application.overview import overview
from idp.domain.identity import Permission, Principal

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status", response_model=SystemStatusResponse)
async def system_status(
    _principal: Annotated[Principal, Depends(require(Permission.SYSTEM_READ))],
    container: ContainerDep,
) -> SystemStatusResponse:
    health = await container.health.check()
    return SystemStatusResponse(
        status=health.status,
        environment=container.settings.environment.value,
        version=container.settings.pipeline_version,
        storage_backend=container.storage.name,
        components=[
            ComponentStatusOut(
                name=c.name,
                status=c.status,
                latency_ms=round(c.latency_ms, 2) if c.latency_ms is not None else None,
                detail=c.detail,
                metadata=c.metadata,
            )
            for c in health.components
        ],
    )


@router.get("/overview")
async def get_overview(
    principal: Annotated[Principal, Depends(require(Permission.DOCUMENTS_READ))],
    session: SessionDep,
) -> dict[str, Any]:
    """Operational overview of the caller's tenant (last 30 days)."""
    return await overview(session, principal)
