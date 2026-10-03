"""Unauthenticated liveness/readiness probes for orchestrators.

They intentionally expose no component details; those live behind
`GET /system/status` which requires authentication.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from idp.api.deps import ContainerDep
from idp.api.schemas.system import LivenessResponse, ReadinessResponse
from idp.domain.health import HealthStatus

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    return LivenessResponse(status=HealthStatus.UP)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def ready(container: ContainerDep, response: Response) -> ReadinessResponse:
    health = await container.health.check()
    if not health.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status=health.status)
