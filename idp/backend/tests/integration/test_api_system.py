import time

import httpx
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from idp.container import Container
from idp.infrastructure.queue.redis import (
    DEFAULT_QUEUE_NAME,
    WorkerHeartbeat,
    check_workers,
    write_heartbeat,
)
from tests.conftest import BACKEND_ROOT
from tests.integration.conftest import TenantFixture, login


async def test_liveness(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "up"}


async def test_readiness_is_ready_but_degraded_without_workers(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    # Public probe exposes no component detail.
    assert response.json() == {"status": "degraded"}


async def test_correlation_id_is_propagated_or_minted(client: httpx.AsyncClient) -> None:
    supplied = await client.get(
        "/api/v1/health/live", headers={"X-Correlation-ID": "abc-123-def-456"}
    )
    assert supplied.headers["X-Correlation-ID"] == "abc-123-def-456"
    hostile = await client.get(
        "/api/v1/health/live", headers={"X-Correlation-ID": "<script>alert(1)</script>"}
    )
    assert hostile.headers["X-Correlation-ID"] != "<script>alert(1)</script>"
    assert len(hostile.headers["X-Correlation-ID"]) == 32


async def test_security_headers(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


async def test_unknown_route_is_problem_json(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error_category"] == "NOT_FOUND"


async def test_system_status_reports_components_and_workers(
    client: httpx.AsyncClient, container: Container, acme: TenantFixture
) -> None:
    owner = await login(client, acme.owner_email, acme.owner_password)
    body = (await client.get("/api/v1/system/status", headers=owner)).json()
    components = {c["name"]: c for c in body["components"]}
    assert set(components) == {"database", "redis", "storage", "workers"}
    assert components["database"]["status"] == "up"
    head = ScriptDirectory.from_config(AlembicConfig(str(BACKEND_ROOT / "alembic.ini")))
    assert components["database"]["metadata"]["schema_revision"] == head.get_current_head()
    assert components["workers"]["status"] == "degraded"
    assert body["status"] == "degraded"
    assert body["environment"] == "test"

    now = time.time()
    await write_heartbeat(
        container.redis,
        WorkerHeartbeat(
            worker_id="w1:1",
            hostname="w1",
            pid=1,
            version="0.1.0",
            max_jobs=4,
            started_at=now,
            last_seen_at=now,
        ),
        ttl_seconds=30,
    )
    body = (await client.get("/api/v1/system/status", headers=owner)).json()
    workers = next(c for c in body["components"] if c["name"] == "workers")
    assert workers["status"] == "up"
    assert workers["metadata"]["count"] == 1
    assert body["status"] == "up"


async def test_openapi_is_published(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/api/v1/openapi.json")).json()
    assert "/api/v1/auth/login" in spec["paths"]
    assert "/api/v1/storage/local/{key}" not in spec["paths"]


async def test_queue_depth_counts_only_due_jobs(container: Container) -> None:
    now_ms = int(time.time() * 1000)
    await container.redis.zadd(
        DEFAULT_QUEUE_NAME, {"due-job": now_ms - 1000, "scheduled-cron": now_ms + 60_000}
    )
    health = await check_workers(container.redis)
    assert health.metadata["queued_jobs"] == 1
