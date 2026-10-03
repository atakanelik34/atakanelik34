"""Processing pipeline mechanics: claim, checkpoints, retries, dead-letter, replay,
crash recovery, fencing, sweeper, and a real arq worker end to end."""

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from arq import func
from arq.worker import Worker
from sqlalchemy import func as sql_func
from sqlalchemy import select, update

from idp.application import workflows
from idp.application.jobs import JobRunner, JobScheduler, JobSweeper, RunOutcome
from idp.application.workflows import StepContext, StepResult, WorkflowDefinition
from idp.container import Container
from idp.domain.errors import ProviderError
from idp.domain.lifecycle import DocumentStatus, JobStatus, StepStatus
from idp.infrastructure.db.models import (
    AuditLog,
    Document,
    DocumentPage,
    ProcessingJob,
    ProcessingStep,
)
from idp.infrastructure.queue.jobs import PROCESS_JOB_FUNCTION, ArqJobQueue
from tests.fixtures import files
from tests.integration.conftest import RecordingQueue, TenantFixture, login, upload

RunnerFactory = Callable[..., JobRunner]


@pytest.fixture
async def owner(client: httpx.AsyncClient, acme: TenantFixture) -> dict[str, str]:
    return await login(client, acme.owner_email, acme.owner_password)


async def _upload(
    client: httpx.AsyncClient, owner: dict[str, str], data: bytes
) -> tuple[str, uuid.UUID]:
    response = await upload(client, owner, data)
    assert response.status_code == 201, response.text
    body = response.json()
    return body["document"]["id"], uuid.UUID(body["job_id"])


async def _job(container: Container, job_id: uuid.UUID) -> ProcessingJob:
    async with container.session_factory() as session:
        job = await session.get(ProcessingJob, job_id)
        assert job is not None
        return job


async def _document(container: Container, doc_id: str) -> Document:
    async with container.session_factory() as session:
        doc = await session.get(Document, uuid.UUID(doc_id))
        assert doc is not None
        return doc


async def _steps(container: Container, job_id: uuid.UUID) -> list[ProcessingStep]:
    async with container.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(ProcessingStep)
                    .where(ProcessingStep.job_id == job_id)
                    .order_by(ProcessingStep.attempt, ProcessingStep.started_at)
                )
            ).all()
        )


async def _make_due(container: Container, job_id: uuid.UUID) -> None:
    async with container.session_factory() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(next_attempt_at=sql_func.now() - timedelta(seconds=1))
        )
        await session.commit()


class FailingStep:
    key = "probe"

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    async def run(self, ctx: StepContext) -> StepResult:
        self.calls += 1
        raise self.exc


# --- happy path ----------------------------------------------------------------


async def test_native_pdf_flows_to_completed_with_audited_timeline(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner: RunnerFactory,
) -> None:
    doc_id, job_id = await _upload(client, owner, files.native_pdf(2))

    assert await make_runner().run(job_id) is RunOutcome.SUCCEEDED

    job = await _job(container, job_id)
    assert (job.status, job.attempts, job.lease_expires_at) == (JobStatus.SUCCEEDED, 1, None)
    assert (await _document(container, doc_id)).status is DocumentStatus.COMPLETED
    step = next(s for s in await _steps(container, job_id) if s.step_key == "probe")
    assert step.status is StepStatus.SUCCEEDED
    assert step.metrics == {"page_count": 2, "pages_with_text": 2, "text_layer": "native"}
    assert step.provider == "pdfium-pillow-probe"

    detail = (await client.get(f"/api/v1/documents/{doc_id}", headers=owner)).json()
    assert detail["page_count"] == 2
    assert [p["has_text_layer"] for p in detail["pages"]] == [True, True]

    timeline = (await client.get(f"/api/v1/documents/{doc_id}/timeline", headers=owner)).json()
    moves = [(c["from_status"], c["to_status"]) for c in timeline["status_changes"]]
    assert moves == [("RECEIVED", "QUEUED"), ("QUEUED", "PROCESSING"), ("PROCESSING", "COMPLETED")]
    assert timeline["jobs"][0]["steps"][0]["step_key"] == "probe"


async def test_scanned_pdf_is_flagged_for_ocr(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner: RunnerFactory,
) -> None:
    _, job_id = await _upload(client, owner, files.scanned_pdf(3))
    assert await make_runner().run(job_id) is RunOutcome.SUCCEEDED
    step = next(s for s in await _steps(container, job_id) if s.step_key == "probe")
    assert step.metrics["text_layer"] == "none"


async def test_duplicate_delivery_is_a_no_op(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner: RunnerFactory,
) -> None:
    _, job_id = await _upload(client, owner, files.native_pdf())
    runner = make_runner()
    assert await runner.run(job_id) is RunOutcome.SUCCEEDED
    assert await runner.run(job_id) is RunOutcome.NOT_CLAIMED
    assert await runner.run(uuid.uuid4()) is RunOutcome.NOT_CLAIMED
    assert len(await _steps(container, job_id)) == 2  # probe + digitize, once each
    assert (await _job(container, job_id)).attempts == 1


# --- failures ------------------------------------------------------------------


async def test_corrupted_document_fails_without_retry(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner: RunnerFactory,
    queue: RecordingQueue,
) -> None:
    doc_id, job_id = await _upload(client, owner, files.corrupted_pdf())
    assert await make_runner().run(job_id) is RunOutcome.FAILED

    job = await _job(container, job_id)
    assert (job.status, job.last_error_category, job.attempts) == (
        JobStatus.FAILED,
        "DOCUMENT_ERROR",
        1,
    )
    assert (await _document(container, doc_id)).status is DocumentStatus.FAILED
    [step] = await _steps(container, job_id)
    assert (step.status, step.error_code) == (StepStatus.FAILED, "document_error")
    assert len(queue.messages) == 1  # only the upload dispatch; no retry
    async with container.session_factory() as session:
        audited = await session.scalar(
            select(AuditLog.action).where(AuditLog.entity_id == str(job_id))
        )
    assert audited == "job.failed"


async def test_transient_errors_retry_with_backoff_then_dead_letter_and_replay(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner: RunnerFactory,
    queue: RecordingQueue,
) -> None:
    doc_id, job_id = await _upload(client, owner, files.native_pdf())
    flaky = FailingStep(ProviderError("OCR service timed out"))
    runner = make_runner({"probe": flaky})

    assert await runner.run(job_id) is RunOutcome.RETRY_SCHEDULED
    job = await _job(container, job_id)
    assert (job.status, job.attempts) == (JobStatus.RETRY_SCHEDULED, 1)
    assert job.next_attempt_at is not None
    assert (await _document(container, doc_id)).status is DocumentStatus.PROCESSING
    retry_message = queue.messages[-1]
    assert retry_message[:2] == (job_id, 1)
    assert retry_message[2] > 0  # deferred by the backoff

    # Not due yet: an early (or duplicated) message claims nothing.
    assert await runner.run(job_id) is RunOutcome.NOT_CLAIMED

    await _make_due(container, job_id)
    assert await runner.run(job_id) is RunOutcome.RETRY_SCHEDULED
    await _make_due(container, job_id)
    assert await runner.run(job_id) is RunOutcome.DEAD_LETTERED

    job = await _job(container, job_id)
    assert (job.status, job.attempts, job.last_error_message) == (
        JobStatus.DEAD_LETTERED,
        3,
        "OCR service timed out",
    )
    assert flaky.calls == 3
    assert (await _document(container, doc_id)).status is DocumentStatus.FAILED
    assert [s.status for s in await _steps(container, job_id)] == [StepStatus.FAILED] * 3

    # Replay creates a new job; the dead-lettered one stays as history.
    replay = await client.post(f"/api/v1/documents/{doc_id}/process", headers=owner)
    assert replay.status_code == 202
    new_job_id = uuid.UUID(replay.json()["id"])
    assert replay.json()["trigger"] == "replay"
    assert await make_runner().run(new_job_id) is RunOutcome.SUCCEEDED
    assert (await _document(container, doc_id)).status is DocumentStatus.COMPLETED
    assert (await _job(container, job_id)).status is JobStatus.DEAD_LETTERED
    timeline = (await client.get(f"/api/v1/documents/{doc_id}/timeline", headers=owner)).json()
    assert [j["status"] for j in timeline["jobs"]] == ["SUCCEEDED", "DEAD_LETTERED"]


async def test_unexpected_exceptions_are_retryable_and_never_leak_details(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner: RunnerFactory,
) -> None:
    _, job_id = await _upload(client, owner, files.native_pdf())
    runner = make_runner({"probe": FailingStep(RuntimeError("secret invoice text 4111"))})
    assert await runner.run(job_id) is RunOutcome.RETRY_SCHEDULED
    job = await _job(container, job_id)
    assert job.last_error_category == "SYSTEM_ERROR"
    assert "4111" not in (job.last_error_message or "")
    [step] = await _steps(container, job_id)
    assert "4111" not in (step.error_message or "")


# --- crash recovery, checkpoints, fencing ---------------------------------------


async def test_crashed_worker_is_recovered_after_lease_expiry(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner: RunnerFactory,
) -> None:
    doc_id, job_id = await _upload(client, owner, files.native_pdf())
    # Simulate a worker that claimed the job, started the step, then died.
    async with container.session_factory() as session:
        job = await session.get(ProcessingJob, job_id)
        assert job is not None
        job.status = JobStatus.RUNNING
        job.attempts = 1
        job.lease_expires_at = sql_func.now() - timedelta(seconds=5)
        session.add(
            ProcessingStep(
                tenant_id=job.tenant_id,
                job_id=job_id,
                step_key="probe",
                attempt=1,
                status=StepStatus.RUNNING,
                started_at=sql_func.now(),
            )
        )
        doc = await session.get(Document, uuid.UUID(doc_id))
        assert doc is not None
        doc.status = DocumentStatus.PROCESSING
        await session.commit()

    assert await make_runner().run(job_id) is RunOutcome.SUCCEEDED
    steps = [s for s in await _steps(container, job_id) if s.step_key == "probe"]
    assert [(s.attempt, s.status, s.error_code) for s in steps] == [
        (1, StepStatus.FAILED, "worker_lost"),
        (2, StepStatus.SUCCEEDED, None),
    ]
    assert (await _job(container, job_id)).attempts == 2


async def test_running_job_with_live_lease_is_not_stolen(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner: RunnerFactory,
) -> None:
    _, job_id = await _upload(client, owner, files.native_pdf())
    async with container.session_factory() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(
                status=JobStatus.RUNNING,
                attempts=1,
                lease_expires_at=sql_func.now() + timedelta(minutes=5),
            )
        )
        await session.commit()
    assert await make_runner().run(job_id) is RunOutcome.NOT_CLAIMED


class CountingStep:
    def __init__(self, key: str, fail_first: bool = False) -> None:
        self.key = key
        self.calls = 0
        self.fail_first = fail_first

    async def run(self, ctx: StepContext) -> StepResult:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise ProviderError("transient")
        return StepResult(provider="test", metrics={"calls": self.calls})


async def test_completed_steps_are_checkpointed_across_attempts(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner: RunnerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = workflows.DEFAULT_WORKFLOW
    two_steps = WorkflowDefinition(
        key=default.key, version=default.version, steps=("first", "second")
    )
    monkeypatch.setitem(workflows._REGISTRY, (default.key, default.version), two_steps)
    first, second = CountingStep("first"), CountingStep("second", fail_first=True)
    runner = make_runner({"first": first, "second": second})
    _, job_id = await _upload(client, owner, files.native_pdf())

    assert await runner.run(job_id) is RunOutcome.RETRY_SCHEDULED
    await _make_due(container, job_id)
    assert await runner.run(job_id) is RunOutcome.SUCCEEDED

    assert (first.calls, second.calls) == (1, 2)
    steps = [(s.step_key, s.attempt, s.status) for s in await _steps(container, job_id)]
    assert steps == [
        ("first", 1, StepStatus.SUCCEEDED),
        ("second", 1, StepStatus.FAILED),
        ("second", 2, StepStatus.SUCCEEDED),
    ]


async def test_worker_that_lost_its_lease_cannot_write_results(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    make_runner: RunnerFactory,
) -> None:
    _, job_id = await _upload(client, owner, files.native_pdf())

    class StolenLeaseStep:
        key = "probe"

        async def run(self, ctx: StepContext) -> StepResult:
            # While this worker is busy, its lease expires and another worker
            # reclaims the job (attempts moves on).
            async with container.session_factory() as other:
                await other.execute(
                    update(ProcessingJob).where(ProcessingJob.id == job_id).values(attempts=2)
                )
                await other.commit()
            ctx.session.add(
                DocumentPage(
                    tenant_id=ctx.document.tenant_id,
                    document_id=ctx.document.id,
                    page_number=1,
                    width=1,
                    height=1,
                    unit="pt",
                    rotation=0,
                    has_text_layer=False,
                    char_count=0,
                )
            )
            return StepResult()

    assert await make_runner({"probe": StolenLeaseStep()}).run(job_id) is RunOutcome.LOST_LEASE
    async with container.session_factory() as session:
        pages = (await session.scalars(select(DocumentPage))).all()
    assert pages == []  # the stale worker's writes were rolled back
    [step] = await _steps(container, job_id)
    assert step.status is StepStatus.RUNNING  # left for the new owner / sweeper


# --- sweeper and lost messages ------------------------------------------------------


async def test_enqueue_outage_does_not_lose_uploads(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    queue: RecordingQueue,
    sweeper: JobSweeper,
) -> None:
    queue.fail = True
    _, job_id = await _upload(client, owner, files.native_pdf())
    assert queue.messages == []
    assert (await _job(container, job_id)).status is JobStatus.QUEUED

    queue.fail = False
    assert await sweeper.sweep() == 0  # still inside the grace period
    async with container.session_factory() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .values(updated_at=sql_func.now() - timedelta(minutes=10))
        )
        await session.commit()
    assert await sweeper.sweep() == 1
    assert queue.messages == [(job_id, 0, 0)]


async def test_sweeper_recovers_expired_leases_and_overdue_retries(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    queue: RecordingQueue,
    sweeper: JobSweeper,
) -> None:
    _, dead_worker_job = await _upload(client, owner, files.native_pdf(1))
    _, overdue_job = await _upload(client, owner, files.native_pdf(2))
    _, healthy_job = await _upload(client, owner, files.native_pdf(3))
    queue.messages.clear()
    async with container.session_factory() as session:
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == dead_worker_job)
            .values(
                status=JobStatus.RUNNING,
                attempts=1,
                lease_expires_at=sql_func.now() - timedelta(seconds=1),
            )
        )
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == overdue_job)
            .values(
                status=JobStatus.RETRY_SCHEDULED,
                attempts=2,
                next_attempt_at=sql_func.now() - timedelta(minutes=10),
            )
        )
        await session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == healthy_job)
            .values(
                status=JobStatus.RUNNING,
                attempts=1,
                lease_expires_at=sql_func.now() + timedelta(minutes=10),
            )
        )
        await session.commit()

    assert await sweeper.sweep() == 2
    assert sorted(queue.messages) == sorted([(dead_worker_job, 1, 0), (overdue_job, 2, 0)])


# --- real arq worker, end to end ---------------------------------------------------


async def test_end_to_end_through_redis_and_arq_worker(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    container: Container,
    settings: Any,
    default_handlers: dict[str, Any],
) -> None:
    real_scheduler = JobScheduler(ArqJobQueue(container.redis), settings)
    container.scheduler = real_scheduler
    doc_id, job_id = await _upload(client, owner, files.native_pdf(2))
    runner = JobRunner(container.session_factory, real_scheduler, default_handlers, settings)

    async def process_job(ctx: dict[str, Any], job_id: str) -> str:
        return (await runner.run(uuid.UUID(job_id))).value

    worker = Worker(
        functions=[func(process_job, name=PROCESS_JOB_FUNCTION, max_tries=1, keep_result=0)],
        redis_pool=container.redis,
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
    )
    await worker.main()
    await worker.close()

    assert (await _document(container, doc_id)).status is DocumentStatus.COMPLETED
    assert (await _job(container, job_id)).status is JobStatus.SUCCEEDED
    # Re-dispatching the same job state is de-duplicated by message id or a no-op claim.
    await real_scheduler.dispatch(job_id, token=0)
