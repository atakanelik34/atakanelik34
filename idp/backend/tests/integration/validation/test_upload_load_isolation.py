"""Scenarios 1, 2, 14: upload rate limit, concurrent upload flood, tenant isolation under load."""

import asyncio
import statistics
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from idp.application.jobs import RunOutcome
from idp.config import Settings
from idp.container import Container
from tests.conftest import make_settings
from tests.fixtures import files
from tests.integration.conftest import RecordingQueue, bootstrap_tenant, login, upload

UPLOAD_LIMIT = 5
FLOOD = 200


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Tight upload limit to observe enforcement; a generous API limit for the flood.
    return make_settings(
        tmp_path, upload_rate_limit_per_minute=UPLOAD_LIMIT, api_rate_limit_per_minute=10_000
    )


def _unique_pdf(tag: str) -> bytes:
    return files.make_pdf([f"Invoice {tag}\nTotal due 100.00 EUR"])


async def _timed_upload(client, headers, data):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    try:
        response = await upload(client, headers, data)
        status: int | str = response.status_code
    except Exception as exc:
        status = type(exc).__name__
    return status, time.perf_counter() - started


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)


# 1 --------------------------------------------------------------------------------------
# F1 (fixed): UPLOAD_RATE_LIMIT_PER_MINUTE is enforced per principal.
async def test_upload_rate_limit_is_enforced_per_principal(client, acme, record) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    statuses = [
        (await upload(client, headers, _unique_pdf(f"rl-{i}"))).status_code
        for i in range(UPLOAD_LIMIT + 3)
    ]
    record("1 upload rate limit (F1 fixed)", limit_per_minute=UPLOAD_LIMIT, statuses=statuses)
    assert statuses[:UPLOAD_LIMIT] == [201] * UPLOAD_LIMIT
    assert set(statuses[UPLOAD_LIMIT:]) == {429}


async def test_api_rate_limit_still_bounds_upload_floods(client, acme, container, record) -> None:  # type: ignore[no-untyped-def]
    """The general per-principal API limit also bounds uploads (both limits apply)."""
    headers = await login(client, acme.owner_email, acme.owner_password)
    container.api_limiter._limit = 10
    statuses = [
        (await upload(client, headers, _unique_pdf(f"api-{i}"))).status_code for i in range(14)
    ]
    assert 429 in statuses and statuses.count(201) <= 10
    record("1 API limit bounds uploads", statuses=statuses)


# 2 --------------------------------------------------------------------------------------
async def test_concurrent_upload_flood(
    client, acme, container, queue: RecordingQueue, record
) -> None:  # type: ignore[no-untyped-def]
    headers = await login(client, acme.owner_email, acme.owner_password)
    # A load test of the upload path, not of the limiter (scenario 1 covers that).
    container.upload_limiter._limit = 10_000
    started = time.perf_counter()
    results = await asyncio.gather(
        *(_timed_upload(client, headers, _unique_pdf(f"flood-{i}")) for i in range(FLOOD))
    )
    wall = time.perf_counter() - started
    statuses = [s for s, _ in results]
    latencies = [t for _, t in results]
    async with container.session_factory() as session:
        documents = await session.scalar(text("SELECT count(*) FROM documents"))
        jobs = await session.scalar(text("SELECT count(*) FROM processing_jobs"))
        dangling = await session.scalar(
            text(
                "SELECT count(*) FROM documents d WHERE NOT EXISTS (SELECT 1 FROM processing_jobs j WHERE j.document_id = d.id)"
            )
        )
    record(
        "2 concurrent upload flood",
        uploads=FLOOD,
        ok=statuses.count(201),
        other=sorted({str(s) for s in statuses if s != 201}),
        documents=documents,
        jobs=jobs,
        dispatched=len(queue.messages),
        wall_s=round(wall, 2),
        uploads_per_s=round(FLOOD / wall, 1),
        p50_s=_percentile(latencies, 0.5),
        p95_s=_percentile(latencies, 0.95),
        max_s=round(max(latencies), 3),
        mean_s=round(statistics.mean(latencies), 3),
    )
    assert statuses.count(201) == FLOOD, sorted({str(s) for s in statuses})
    assert documents == jobs == FLOOD and dangling == 0
    assert len({m[0] for m in queue.messages}) == FLOOD  # every job dispatched once


# 14 -------------------------------------------------------------------------------------
async def _force_rls(container: Container, on: bool) -> None:
    async with container.engine.begin() as conn:
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() AND c.relkind = 'r' AND c.relrowsecurity"
                    )
                )
            )
            .scalars()
            .all()
        )
        verb = "FORCE" if on else "NO FORCE"
        for table in tables:
            await conn.execute(text(f'ALTER TABLE "{table}" {verb} ROW LEVEL SECURITY'))


async def test_tenant_isolation_under_concurrent_load(
    client, acme, container, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    """Two tenants upload, list, read and process concurrently while Postgres enforces RLS
    on every tenant table for this connection role (as it does for idp_app in production).
    Every response must contain only the caller's data; cross-tenant reads must be 404."""
    beta = await bootstrap_tenant(container, "beta")
    ha = await login(client, acme.owner_email, acme.owner_password)
    hb = await login(client, beta.owner_email, beta.owner_password)
    container.upload_limiter._limit = 10_000  # isolation under load, not the limiter
    await _force_rls(container, True)
    try:
        uploads = await asyncio.gather(
            *(upload(client, ha, _unique_pdf(f"a-{i}")) for i in range(30)),
            *(upload(client, hb, _unique_pdf(f"b-{i}")) for i in range(30)),
        )
        assert all(r.status_code == 201 for r in uploads), {r.status_code for r in uploads}
        a_docs = {r.json()["document"]["id"] for r in uploads[:30]}
        b_docs = {r.json()["document"]["id"] for r in uploads[30:]}
        a_jobs = [uuid.UUID(r.json()["job_id"]) for r in uploads[:10]]

        async def read(headers, doc_id):  # type: ignore[no-untyped-def]
            return (await client.get(f"/api/v1/documents/{doc_id}", headers=headers)).status_code

        async def listing(headers):  # type: ignore[no-untyped-def]
            body = (await client.get("/api/v1/documents?limit=100", headers=headers)).json()
            return {d["id"] for d in body["items"]}

        workload = [
            *(read(ha, d) for d in a_docs),
            *(read(hb, d) for d in b_docs),
            *(read(ha, d) for d in b_docs),  # cross-tenant
            *(read(hb, d) for d in a_docs),  # cross-tenant
            *(listing(ha) for _ in range(20)),
            *(listing(hb) for _ in range(20)),
            *(runner_factory().run(j) for j in a_jobs),  # workers (system context) in parallel
        ]
        results = await asyncio.gather(*workload)
        own_a, own_b = results[0:30], results[30:60]
        cross = results[60:120]
        lists_a, lists_b = results[120:140], results[140:160]
        runs = results[160:]
        assert set(own_a) == {200} and set(own_b) == {200}
        assert set(cross) == {404}
        assert all(seen <= a_docs for seen in lists_a) and all(seen <= b_docs for seen in lists_b)
        assert all(r in (RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW) for r in runs)
        jobs_a = (await client.get("/api/v1/jobs?limit=200", headers=ha)).json()
        assert {j["document_id"] for j in jobs_a} <= a_docs
        record(
            "14 tenant isolation under load (RLS forced)",
            requests=len(workload) + 60,
            cross_tenant_reads=len(cross),
            cross_tenant_leaks=sum(1 for c in cross if c != 404),
            list_leaks=sum(1 for s in lists_a if not s <= a_docs)
            + sum(1 for s in lists_b if not s <= b_docs),
            jobs_processed_in_parallel=len(runs),
        )
    finally:
        await _force_rls(container, False)
