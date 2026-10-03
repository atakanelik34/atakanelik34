"""Health value objects shared by infrastructure adapters and the health service."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class HealthStatus(StrEnum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    name: str
    status: HealthStatus
    latency_ms: float | None = None
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_up(self) -> bool:
        return self.status is HealthStatus.UP
