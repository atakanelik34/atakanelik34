"""Scenarios 5–9: slow OCR, OCR time vs. job lease, worker crash during OCR,
lease expiry during processing, retry after lease expiry."""

import asyncio
import uuid

import httpx
import pytest

from idp.application.jobs import JobSweeper, RunOutcome
from tests.fixtures import files
from tests.integration.conftest import RecordingQueue, TenantFixture, login, upload
from tests.integration.validation.conftest import (
    FaultyOCR,
    Timer,
    expire_lease,
    job_row,
    make_due,
    steps_of,
)

PAGES = 4


async def _scan(client: httpx.AsyncClient, acme: TenantFixture, pages: int = PAGES) -> uuid.UUID:
    headers = await login(client, acme.owner_email, acme.owner_password)
    body = (await upload(client, headers, files.scanned_pdf(pages))).json()
    return uuid.UUID(body["job_id"])


# 5 ---------------------------------------------------------------------------------------
async def test_slow_ocr_within_the_lease_completes(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    job_id = await _scan(client, acme)
    ocr = FaultyOCR(delay_seconds=0.5)
    with Timer() as t:
        outcome = await runner_factory(ocr_handlers(ocr)).run(job_id)
    assert outcome in {RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW}
    assert ocr.calls == PAGES  # sequential, once per page
    assert t.seconds >= PAGES * 0.5
    record("5 slow OCR", pages=PAGES, ocr_delay_s=0.5, wall_s=t.seconds, outcome=outcome.value)


async def test_transient_ocr_failure_retries_the_step(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    job_id = await _scan(client, acme)
    ocr = FaultyOCR(fail_on_calls={2})
    runner = runner_factory(ocr_handlers(ocr))
    assert await runner.run(job_id) is RunOutcome.RETRY_SCHEDULED
    assert (await job_row(container, job_id))["last_error_code"] == "provider_error"
    await make_due(container, job_id)
    assert await runner.run(job_id) in {RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW}
    record("5 OCR transient failure", ocr_calls=ocr.calls, recovered=True)


# 6 + 8 + 9 -------------------------------------------------------------------------------
async def test_ocr_longer_than_lease_is_fenced_and_retried_by_another_worker(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    """The lease (2 s) expires during OCR (4 × 0.8 s). A second worker re-claims the job;
    the first one's results are discarded by the fence; only one completion counts."""
    job_id = await _scan(client, acme)
    ocr = FaultyOCR(delay_seconds=0.8)
    first = asyncio.create_task(runner_factory(ocr_handlers(ocr), job_lease_seconds=2).run(job_id))
    await asyncio.sleep(2.4)  # lease expired, first worker still OCR-ing
    assert (await job_row(container, job_id))["attempts"] == 1
    second = runner_factory(ocr_handlers(ocr), job_lease_seconds=30)
    with Timer() as t:
        outcomes = await asyncio.gather(first, second.run(job_id))
    assert outcomes[0] is RunOutcome.LOST_LEASE
    assert outcomes[1] in {RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW}
    row = await job_row(container, job_id)
    assert row["attempts"] == 2
    async with container.session_factory() as session:
        from sqlalchemy import text

        pages = await session.scalar(
            text(
                "SELECT count(*) FROM document_pages p JOIN processing_jobs j ON j.document_id = p.document_id WHERE j.id = :id"
            ),
            {"id": job_id},
        )
    assert pages == PAGES  # no duplicated page rows
    record(
        "6/8/9 OCR exceeds lease",
        lease_s=2,
        ocr_s_per_page=0.8,
        pages=PAGES,
        ocr_calls=ocr.calls,
        wasted_ocr_calls=ocr.calls - PAGES,
        outcomes=[o.value for o in outcomes],
        finish_s=t.seconds,
    )


async def test_lease_expiry_without_contention_is_harmless(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    """Lease expires but nobody re-claims: the slow worker still completes (fence = attempts)."""
    job_id = await _scan(client, acme, pages=3)
    ocr = FaultyOCR(delay_seconds=0.9)
    outcome = await runner_factory(ocr_handlers(ocr), job_lease_seconds=1).run(job_id)
    assert outcome in {RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW}
    record("8 lease expiry, no contention", outcome=outcome.value)


async def test_sweeper_redispatches_expired_leases(
    client, acme, container, scheduler, settings, queue: RecordingQueue, record
) -> None:  # type: ignore[no-untyped-def]
    job_id = await _scan(client, acme)
    async with container.session_factory() as session:
        from sqlalchemy import text

        await session.execute(
            text(
                "UPDATE processing_jobs SET status='RUNNING', attempts=1, lease_expires_at=now() - interval '1 second' WHERE id=:id"
            ),
            {"id": job_id},
        )
        await session.commit()
    queue.messages.clear()
    assert await JobSweeper(container.session_factory, scheduler, settings).sweep() == 1
    assert queue.messages[0][0] == job_id
    record("9 sweeper re-dispatch", redispatched=len(queue.messages))


# 7 ---------------------------------------------------------------------------------------
async def test_worker_crash_during_ocr_recovers(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    """Kill the worker mid-OCR (task cancelled: the DB is left exactly as after a crash)."""
    job_id = await _scan(client, acme)
    ocr = FaultyOCR(delay_seconds=0.5)
    crashed = asyncio.create_task(runner_factory(ocr_handlers(ocr)).run(job_id))
    await ocr.started.wait()
    await asyncio.sleep(0.2)
    crashed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await crashed
    row = await job_row(container, job_id)
    assert str(row["status"]) == "RUNNING"  # stuck until the lease expires
    # A new worker cannot steal a live lease...
    assert await runner_factory(ocr_handlers(FaultyOCR())).run(job_id) is RunOutcome.NOT_CLAIMED
    # ...but takes over once it expires.
    await expire_lease(container, job_id)
    recovery = FaultyOCR()
    outcome = await runner_factory(ocr_handlers(recovery)).run(job_id)
    assert outcome in {RunOutcome.SUCCEEDED, RunOutcome.WAITING_FOR_REVIEW}
    steps = await steps_of(container, job_id)
    assert ("digitize", "FAILED", "worker_lost") in steps
    assert sum(1 for s in steps if s[0] == "digitize" and s[1] == "SUCCEEDED") == 1
    assert sum(1 for s in steps if s[0] == "probe" and s[1] == "SUCCEEDED") == 1  # checkpoint kept
    record("7 worker crash during OCR", steps=steps, recovered=True)


# 6 / 9: F2 (fixed): max_attempts bounds lease-expiry re-claims ------------------------------
async def test_step_that_always_outlasts_the_lease_is_dead_lettered(
    client, acme, container, ocr_handlers, runner_factory, record
) -> None:  # type: ignore[no-untyped-def]
    job_id = await _scan(client, acme, pages=3)
    max_attempts = (await job_row(container, job_id))["max_attempts"]
    claims = 0  # deliveries that actually ran the workflow (OCR started)
    outcomes = []
    for _ in range(max_attempts + 2):
        ocr = FaultyOCR(delay_seconds=1.0)
        task = asyncio.create_task(
            runner_factory(ocr_handlers(ocr), job_lease_seconds=1).run(job_id)
        )
        await asyncio.sleep(1.4)
        task.cancel()  # the worker's own time limit (arq timeout = lease) kills it
        try:
            outcome = await task
        except asyncio.CancelledError:
            outcome = None
        outcomes.append(outcome.value if outcome else "killed")
        if ocr.calls:
            claims += 1
        await expire_lease(container, job_id)
    row = await job_row(container, job_id)
    record(
        "6/9 step always outlasts lease (F2 fixed)",
        claims=claims,
        max_attempts=max_attempts,
        attempts=row["attempts"],
        outcomes=outcomes,
        final=str(row["status"]),
        error=row["last_error_code"],
    )
    assert str(row["status"]) == "DEAD_LETTERED"
    assert row["attempts"] == max_attempts
    assert claims <= max_attempts
    assert outcomes[max_attempts] == "dead_lettered"
    assert set(outcomes[max_attempts + 1 :]) == {"not_claimed"}
