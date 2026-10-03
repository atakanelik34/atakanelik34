import asyncio
import json
import logging

import pytest

from idp.application import health as health_module
from idp.application.health import HealthService
from idp.config import LogFormat
from idp.domain.health import ComponentHealth, HealthStatus
from idp.infrastructure.logging import REDACTED, configure_logging, get_logger


def _probe(name: str, status: HealthStatus):  # type: ignore[no-untyped-def]
    async def probe() -> ComponentHealth:
        return ComponentHealth(name=name, status=status)

    return probe


async def test_all_up() -> None:
    service = HealthService(
        critical={"db": _probe("db", HealthStatus.UP)},
        optional={"workers": _probe("workers", HealthStatus.UP)},
    )
    result = await service.check()
    assert result.status is HealthStatus.UP
    assert result.ready


async def test_optional_failure_only_degrades() -> None:
    service = HealthService(
        critical={"db": _probe("db", HealthStatus.UP)},
        optional={"workers": _probe("workers", HealthStatus.DEGRADED)},
    )
    result = await service.check()
    assert result.status is HealthStatus.DEGRADED
    assert result.ready


async def test_critical_failure_and_exceptions_are_down() -> None:
    async def boom() -> ComponentHealth:
        raise ConnectionError("refused")

    service = HealthService(critical={"db": boom}, optional={})
    result = await service.check()
    assert result.status is HealthStatus.DOWN
    assert not result.ready
    assert result.components[0].detail == "ConnectionError"


async def test_slow_probe_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_module, "PROBE_TIMEOUT_SECONDS", 0.01)

    async def slow() -> ComponentHealth:
        await asyncio.sleep(1)
        return ComponentHealth(name="db", status=HealthStatus.UP)

    result = await HealthService(critical={"db": slow}, optional={}).check()
    assert result.components[0].detail == "timeout"


def test_sensitive_keys_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", LogFormat.JSON)
    get_logger("test").info(
        "evt", password="hunter2", text="invoice body", Authorization="Bearer x", user_id="u1"
    )
    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(line)
    assert record["password"] == REDACTED
    assert record["text"] == REDACTED
    assert record["Authorization"] == REDACTED
    assert record["user_id"] == "u1"
    logging.getLogger().handlers.clear()
