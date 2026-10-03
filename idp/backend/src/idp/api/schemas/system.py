from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from idp.domain.health import HealthStatus


class LivenessResponse(BaseModel):
    status: HealthStatus


class ReadinessResponse(BaseModel):
    status: HealthStatus


class ComponentStatusOut(BaseModel):
    name: str
    status: HealthStatus
    latency_ms: float | None
    detail: str | None
    metadata: dict[str, Any]


class SystemStatusResponse(BaseModel):
    status: HealthStatus
    environment: str
    version: str
    storage_backend: str
    components: list[ComponentStatusOut]
