"""Readiness aggregation across infrastructure dependencies."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from idp.domain.health import ComponentHealth, HealthStatus

HealthProbe = Callable[[], Awaitable[ComponentHealth]]

PROBE_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class SystemHealth:
    status: HealthStatus
    components: list[ComponentHealth]

    @property
    def ready(self) -> bool:
        return self.status is not HealthStatus.DOWN


class HealthService:
    """Runs probes concurrently with a timeout each.

    `critical` probes (database, redis, storage) make the service DOWN when they
    fail; non-critical probes (workers) only degrade it — the API can still
    accept and store documents while workers are restarting.
    """

    def __init__(self, *, critical: dict[str, HealthProbe], optional: dict[str, HealthProbe]):
        self._critical = critical
        self._optional = optional

    @staticmethod
    async def _run(name: str, probe: HealthProbe) -> ComponentHealth:
        try:
            return await asyncio.wait_for(probe(), timeout=PROBE_TIMEOUT_SECONDS)
        except TimeoutError:
            return ComponentHealth(name=name, status=HealthStatus.DOWN, detail="timeout")
        except Exception as exc:
            return ComponentHealth(name=name, status=HealthStatus.DOWN, detail=type(exc).__name__)

    async def check(self) -> SystemHealth:
        names = [*self._critical, *self._optional]
        probes = [*self._critical.values(), *self._optional.values()]
        results = list(
            await asyncio.gather(*(self._run(n, p) for n, p in zip(names, probes, strict=True)))
        )
        critical_results = results[: len(self._critical)]
        if any(r.status is HealthStatus.DOWN for r in critical_results):
            status = HealthStatus.DOWN
        elif any(r.status is not HealthStatus.UP for r in results):
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.UP
        return SystemHealth(status=status, components=results)
