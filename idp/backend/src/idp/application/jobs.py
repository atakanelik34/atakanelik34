"""Job scheduling, execution and recovery.

Postgres is the source of truth; queue messages are only wake-up calls. See
ARCHITECTURE.md §7 "Durability and idempotency" for the full contract:

* `JobScheduler` creates job rows (caller commits) and dispatches *after* commit.
* `JobRunner` claims a job with an atomic lease, runs the workflow's steps with a
  commit per step, and applies the retry / dead-letter policy. The job's
  `attempts` value acts as a fencing token: every write re-checks it, so a
  worker whose lease was taken over can never overwrite the new owner's work.
* `JobSweeper` re-dispatches jobs whose messages were lost or whose worker died.
* `max_attempts` (per job row) is a hard bound: a claim never starts attempt
  `max_attempts + 1`. A job whose lease expired on its final attempt (the
  worker died or was killed by its time limit) is dead-lettered by whoever
  finds it — a runner's claim or the sweeper — instead of being re-run (F2).
* While a step runs, a heartbeat renews the lease every `job_heartbeat_seconds`
  (fenced by `attempts`), so the lease only measures worker liveness; how long
  an attempt may take is bounded by `job_timeout_seconds` and the steps' own
  budgets (F15). A dead worker stops renewing and its lease expires.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Mapping
from datetime import timedelta
from enum import StrEnum
from typing import Any, TypeVar

import structlog
from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from idp.application.audit import ActorType, AuditAction, AuditEntity, record_audit
from idp.application.document_status import change_document_status
from idp.application.workflows import (
    DEFAULT_WORKFLOW,
    AwaitingHumanReview,
    StepContext,
    StepHandler,
    get_workflow,
)
from idp.config import Settings
from idp.context import current_correlation_id
from idp.domain.errors import ConfigurationError, ErrorCategory, IDPError, InternalError
from idp.domain.identity import Principal
from idp.domain.lifecycle import DocumentStatus, JobStatus, StepStatus
from idp.domain.retry import RetryPolicy
from idp.infrastructure.db.models import Document, ProcessingJob, ProcessingStep
from idp.infrastructure.logging import get_logger
from idp.infrastructure.queue.jobs import JobQueue

log = get_logger(__name__)

_T = TypeVar("_T")

SWEEP_BATCH = 500
_UNEXPECTED_ERROR_MESSAGE = "Unexpected internal error"


class JobTrigger(StrEnum):
    UPLOAD = "upload"
    REPROCESS = "reprocess"
    REPLAY = "replay"
    REVIEW_SEND_BACK = "review_send_back"


class RunOutcome(StrEnum):
    NOT_CLAIMED = "not_claimed"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    LOST_LEASE = "lost_lease"
    WAITING_FOR_REVIEW = "waiting_for_review"


class _LeaseLostError(Exception):
    pass


def _require(value: _T | None, what: str) -> _T:
    """Rows the runner itself created/claimed must exist; anything else is a bug."""
    if value is None:
        raise InternalError(f"{what} disappeared during job execution")
    return value


def retry_policy(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.job_max_attempts,
        base_seconds=settings.job_retry_base_seconds,
        max_seconds=settings.job_retry_max_seconds,
    )


def _classify(exc: Exception) -> tuple[ErrorCategory, str, str]:
    if isinstance(exc, IDPError):
        return exc.category, exc.code, exc.message
    # Unknown failures are treated as transient system errors (retryable) and
    # their text is not persisted: it may contain document content.
    return ErrorCategory.SYSTEM_ERROR, "internal_error", _UNEXPECTED_ERROR_MESSAGE


def _exhausted(now: Any) -> ColumnElement[bool]:
    """Jobs that would be claimable, except that their attempt budget is spent."""
    pj = ProcessingJob
    return and_(
        pj.attempts >= pj.max_attempts,
        or_(
            pj.status.in_([JobStatus.QUEUED, JobStatus.RETRY_SCHEDULED]),
            and_(pj.status == JobStatus.RUNNING, pj.lease_expires_at < now),
        ),
    )


async def _close_lost_steps(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Steps left RUNNING by a worker that died are closed out, not left dangling."""
    await session.execute(
        update(ProcessingStep)
        .where(ProcessingStep.job_id == job_id, ProcessingStep.status == StepStatus.RUNNING)
        .values(
            status=StepStatus.FAILED,
            finished_at=func.now(),
            error_category=ErrorCategory.SYSTEM_ERROR.value,
            error_code="worker_lost",
            error_message="Worker stopped before the step finished (lease expired)",
        )
        .execution_options(synchronize_session=False)
    )


async def dead_letter_exhausted(
    session: AsyncSession, job_id: uuid.UUID | None = None
) -> list[uuid.UUID]:
    """Dead-letter jobs whose budget is spent (one job, or a sweep batch). Caller commits."""
    pj = ProcessingJob
    query = select(pj).where(_exhausted(func.now()))
    if job_id is not None:
        query = query.where(pj.id == job_id)
    jobs = (
        await session.scalars(
            query.order_by(pj.updated_at).limit(SWEEP_BATCH).with_for_update(skip_locked=True)
        )
    ).all()
    for job in jobs:
        lost = job.status is JobStatus.RUNNING
        code = "lease_expired" if lost else "attempts_exhausted"
        job.status = JobStatus.DEAD_LETTERED
        job.last_error_category = ErrorCategory.SYSTEM_ERROR.value
        job.last_error_code = code
        job.last_error_message = (
            "The worker stopped on the final attempt (lease expired)"
            if lost
            else "No attempts left"
        )
        job.lease_expires_at = None
        job.next_attempt_at = None
        job.finished_at = func.now()
        await _close_lost_steps(session, job.id)
        document = await session.get(Document, job.document_id, with_for_update=True)
        if document is not None and document.status is DocumentStatus.QUEUED:
            change_document_status(
                session, document, DocumentStatus.PROCESSING, reason="processing started"
            )
        if document is not None and document.status is DocumentStatus.PROCESSING:
            change_document_status(
                session, document, DocumentStatus.FAILED, reason=f"system_error: {code}"
            )
        record_audit(
            session,
            action=AuditAction.JOB_DEAD_LETTERED,
            entity_type=AuditEntity.JOB,
            entity_id=job.id,
            tenant_id=job.tenant_id,
            actor_type=ActorType.SYSTEM,
            after={
                "attempts": job.attempts,
                "error_category": ErrorCategory.SYSTEM_ERROR.value,
                "error_code": code,
            },
        )
        log.warning("job.dead_lettered", job_id=str(job.id), code=code, attempts=job.attempts)
    return [job.id for job in jobs]


class JobScheduler:
    def __init__(self, queue: JobQueue, settings: Settings) -> None:
        self._queue = queue
        self._settings = settings

    def new_job(
        self, document: Document, *, trigger: JobTrigger, requested_by: Principal | None
    ) -> ProcessingJob:
        """Build a QUEUED job for the default workflow. The caller adds and commits it."""
        workflow = DEFAULT_WORKFLOW
        return ProcessingJob(
            id=uuid.uuid4(),
            tenant_id=document.tenant_id,
            document_id=document.id,
            workflow_key=workflow.key,
            workflow_version=workflow.version,
            pipeline_version=self._settings.pipeline_version,
            trigger=trigger.value,
            status=JobStatus.QUEUED,
            attempts=0,
            max_attempts=self._settings.job_max_attempts,
            correlation_id=current_correlation_id(),
            requested_by_id=requested_by.user_id if requested_by else None,
        )

    async def dispatch(self, job_id: uuid.UUID, *, token: int, defer_seconds: float = 0) -> bool:
        """Enqueue after commit. Never raises: a lost message is recovered by the sweeper."""
        try:
            await self._queue.enqueue(job_id, token=token, defer_seconds=defer_seconds)
        except Exception as exc:
            log.warning("job.enqueue_failed", job_id=str(job_id), error=type(exc).__name__)
            return False
        return True


class JobRunner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        scheduler: JobScheduler,
        handlers: Mapping[str, StepHandler],
        settings: Settings,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._sf = session_factory
        self._scheduler = scheduler
        self._handlers = handlers
        self._lease = timedelta(seconds=settings.job_lease_seconds)
        self._heartbeat = settings.job_heartbeat_seconds
        self._policy = retry_policy(settings)
        self._rng = rng

    async def run(self, job_id: uuid.UUID) -> RunOutcome:
        claimed = await self._claim(job_id)
        if claimed is None:
            async with self._sf() as session, session.begin():
                if await dead_letter_exhausted(session, job_id):
                    return RunOutcome.DEAD_LETTERED
            log.info("job.not_claimed", job_id=str(job_id))
            return RunOutcome.NOT_CLAIMED
        fence, document_id, correlation_id = claimed
        with structlog.contextvars.bound_contextvars(
            job_id=str(job_id),
            document_id=str(document_id),
            correlation_id=correlation_id,
            attempt=fence,
        ):
            log.info("job.started")
            outcome = await self._execute(job_id, fence)
            log.info("job.finished", outcome=outcome.value)
            return outcome

    # --- claim -----------------------------------------------------------------

    async def _claim(self, job_id: uuid.UUID) -> tuple[int, uuid.UUID, str | None] | None:
        now = func.now()
        pj = ProcessingJob
        async with self._sf() as session, session.begin():
            claimable = or_(
                and_(
                    pj.status.in_([JobStatus.QUEUED, JobStatus.RETRY_SCHEDULED]),
                    or_(pj.next_attempt_at.is_(None), pj.next_attempt_at <= now),
                ),
                and_(pj.status == JobStatus.RUNNING, pj.lease_expires_at < now),
            )
            claimable = and_(claimable, pj.attempts < pj.max_attempts)
            row = (
                await session.execute(
                    update(pj)
                    .where(pj.id == job_id, claimable)
                    .values(
                        status=JobStatus.RUNNING,
                        attempts=pj.attempts + 1,
                        lease_expires_at=now + self._lease,
                        next_attempt_at=None,
                        started_at=func.coalesce(pj.started_at, now),
                    )
                    .returning(pj.attempts, pj.document_id, pj.correlation_id)
                    .execution_options(synchronize_session=False)
                )
            ).one_or_none()
            if row is None:
                return None
            fence, document_id, correlation_id = row
            await _close_lost_steps(session, job_id)
            document = await session.get(Document, document_id, with_for_update=True)
            if document is not None and document.status is DocumentStatus.QUEUED:
                change_document_status(
                    session, document, DocumentStatus.PROCESSING, reason="processing started"
                )
        return fence, document_id, correlation_id

    # --- execution -------------------------------------------------------------

    async def _execute(self, job_id: uuid.UUID, fence: int) -> RunOutcome:
        async with self._sf() as session:
            job = _require(await session.get(ProcessingJob, job_id), "job")
            workflow_key, workflow_version = job.workflow_key, job.workflow_version

        try:
            workflow = get_workflow(workflow_key, workflow_version)
        except ConfigurationError as exc:
            return await self._fail(job_id, fence, None, exc, 0)

        for step_key in workflow.steps:
            handler = self._handlers.get(step_key)
            if handler is None:
                error = ConfigurationError(f"No handler registered for step '{step_key}'")
                return await self._fail(job_id, fence, None, error, 0)
            outcome = await self._run_step(job_id, fence, handler)
            if outcome is not None:
                return outcome
        return await self._succeed(job_id, fence, workflow.final_status, workflow_key)

    async def _fenced(self, session: AsyncSession, job_id: uuid.UUID, fence: int) -> ProcessingJob:
        job = await session.get(ProcessingJob, job_id, with_for_update=True, populate_existing=True)
        if job is None or job.status is not JobStatus.RUNNING or job.attempts != fence:
            raise _LeaseLostError
        return job

    async def _run_step(
        self, job_id: uuid.UUID, fence: int, handler: StepHandler
    ) -> RunOutcome | None:
        """Run one step. Returns None to continue, or the job's final outcome."""
        async with self._sf() as session:
            try:
                async with session.begin():
                    job = await self._fenced(session, job_id, fence)
                    done = await session.scalar(
                        select(ProcessingStep.id).where(
                            ProcessingStep.job_id == job_id,
                            ProcessingStep.step_key == handler.key,
                            ProcessingStep.status == StepStatus.SUCCEEDED,
                        )
                    )
                    if done is not None:  # checkpoint from an earlier attempt
                        return None
                    job.current_step = handler.key
                    job.lease_expires_at = func.now() + self._lease
                    step = ProcessingStep(
                        tenant_id=job.tenant_id,
                        job_id=job_id,
                        step_key=handler.key,
                        attempt=fence,
                        status=StepStatus.RUNNING,
                        started_at=func.now(),
                    )
                    session.add(step)
            except _LeaseLostError:
                return RunOutcome.LOST_LEASE
            step_id = step.id

        started = time.perf_counter()
        async with self._sf() as session:
            try:
                current = _require(await session.get(ProcessingJob, job_id), "job")
                document = _require(await session.get(Document, current.document_id), "document")
                # End the read transaction: step I/O (downloads, parsing, OCR) must not
                # hold a connection or locks. Handlers do their I/O first, then write.
                await session.commit()
                beat = asyncio.create_task(self._beat(job_id, fence))
                try:
                    result = await handler.run(StepContext(session, current, document))
                finally:
                    beat.cancel()
                # Fence check + result write happen in one short transaction.
                await self._fenced(session, job_id, fence)
                step_row = _require(await session.get(ProcessingStep, step_id), "step")
                step_row.status = StepStatus.SUCCEEDED
                step_row.provider = result.provider
                step_row.provider_version = result.provider_version
                step_row.metrics = result.metrics
                step_row.finished_at = func.now()
                step_row.duration_ms = int((time.perf_counter() - started) * 1000)
                await session.commit()
            except _LeaseLostError:
                await session.rollback()
                log.warning("job.lease_lost", step=handler.key)
                return RunOutcome.LOST_LEASE
            except AwaitingHumanReview as signal:
                return await self._pause(
                    session,
                    job_id=job_id,
                    fence=fence,
                    step_id=step_id,
                    signal=signal,
                    started=started,
                )
            except Exception as exc:
                await session.rollback()
                duration = int((time.perf_counter() - started) * 1000)
                return await self._fail(job_id, fence, step_id, exc, duration)
        log.info("job.step_succeeded", step=handler.key, metrics=result.metrics)
        return None

    async def _beat(self, job_id: uuid.UUID, fence: int) -> None:
        """Renew the lease while this attempt still owns the job; stop once it does not."""
        pj = ProcessingJob
        while True:
            await asyncio.sleep(self._heartbeat)
            try:
                async with self._sf() as session, session.begin():
                    renewed = await session.execute(
                        update(pj)
                        .where(
                            pj.id == job_id,
                            pj.status == JobStatus.RUNNING,
                            pj.attempts == fence,
                        )
                        .values(lease_expires_at=func.now() + self._lease)
                        .execution_options(synchronize_session=False)
                    )
            except Exception as exc:  # a missed beat is survivable; the lease has slack
                log.warning("job.heartbeat_failed", error=type(exc).__name__)
                continue
            if getattr(renewed, "rowcount", 1) == 0:
                log.warning("job.heartbeat_lost_lease")
                return

    async def _pause(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
        fence: int,
        step_id: uuid.UUID,
        signal: AwaitingHumanReview,
        started: float,
    ) -> RunOutcome:
        """Persist the step's pending writes and park the job for a human."""
        try:
            job = await self._fenced(session, job_id, fence)
            step_row = _require(await session.get(ProcessingStep, step_id), "step")
            step_row.status = StepStatus.WAITING
            step_row.metrics = {"reasons": len(signal.reasons)}
            step_row.duration_ms = int((time.perf_counter() - started) * 1000)
            job.status = JobStatus.WAITING_FOR_REVIEW
            job.lease_expires_at = None
            document = _require(
                await session.get(Document, job.document_id, with_for_update=True), "document"
            )
            change_document_status(session, document, signal.document_status, reason=signal.reason)
            await session.commit()
        except _LeaseLostError:
            await session.rollback()
            return RunOutcome.LOST_LEASE
        log.info("job.waiting_for_review", reasons=len(signal.reasons))
        return RunOutcome.WAITING_FOR_REVIEW

    async def _succeed(
        self, job_id: uuid.UUID, fence: int, final_status: DocumentStatus, workflow_key: str
    ) -> RunOutcome:
        async with self._sf() as session:
            try:
                async with session.begin():
                    job = await self._fenced(session, job_id, fence)
                    job.status = JobStatus.SUCCEEDED
                    job.finished_at = func.now()
                    job.lease_expires_at = None
                    job.current_step = None
                    document = _require(
                        await session.get(Document, job.document_id, with_for_update=True),
                        "document",
                    )
                    change_document_status(
                        session,
                        document,
                        final_status,
                        reason=f"workflow '{workflow_key}' finished",
                    )
            except _LeaseLostError:
                return RunOutcome.LOST_LEASE
        return RunOutcome.SUCCEEDED

    async def _fail(
        self,
        job_id: uuid.UUID,
        fence: int,
        step_id: uuid.UUID | None,
        exc: Exception,
        duration_ms: int,
    ) -> RunOutcome:
        category, code, message = _classify(exc)
        if isinstance(exc, IDPError):
            log.warning("job.step_failed", category=category.value, code=code)
        else:
            log.exception("job.step_crashed", exc_info=exc)

        delay = 0.0
        async with self._sf() as session:
            try:
                async with session.begin():
                    job = await self._fenced(session, job_id, fence)
                    if step_id is not None:
                        step = await session.get(ProcessingStep, step_id)
                        if step is not None:
                            step.status = StepStatus.FAILED
                            step.error_category = category.value
                            step.error_code = code
                            step.error_message = message
                            step.finished_at = func.now()
                            step.duration_ms = duration_ms
                    status = self._policy.outcome(category, job.attempts, job.max_attempts)
                    job.status = status
                    job.last_error_category = category.value
                    job.last_error_code = code
                    job.last_error_message = message
                    job.lease_expires_at = None
                    if status is JobStatus.RETRY_SCHEDULED:
                        delay = self._policy.backoff_seconds(job.attempts, rng=self._rng)
                        job.next_attempt_at = func.now() + timedelta(seconds=delay)
                    else:
                        job.finished_at = func.now()
                        document = _require(
                            await session.get(Document, job.document_id, with_for_update=True),
                            "document",
                        )
                        change_document_status(
                            session, document, DocumentStatus.FAILED, reason=f"{category}: {code}"
                        )
                        record_audit(
                            session,
                            action=AuditAction.JOB_DEAD_LETTERED
                            if status is JobStatus.DEAD_LETTERED
                            else AuditAction.JOB_FAILED,
                            entity_type=AuditEntity.JOB,
                            entity_id=job.id,
                            tenant_id=job.tenant_id,
                            actor_type=ActorType.SYSTEM,
                            after={
                                "attempts": job.attempts,
                                "error_category": category.value,
                                "error_code": code,
                            },
                        )
            except _LeaseLostError:
                return RunOutcome.LOST_LEASE

        if status is JobStatus.RETRY_SCHEDULED:
            await self._scheduler.dispatch(job_id, token=fence, defer_seconds=delay)
            return RunOutcome.RETRY_SCHEDULED
        return RunOutcome.DEAD_LETTERED if status is JobStatus.DEAD_LETTERED else RunOutcome.FAILED


class JobSweeper:
    """Re-dispatches jobs whose wake-up message was lost or whose worker died.

    Safe to run concurrently and repeatedly: message ids are deterministic per
    job state, and the runner's claim ignores anything not claimable.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        scheduler: JobScheduler,
        settings: Settings,
    ) -> None:
        self._sf = session_factory
        self._scheduler = scheduler
        self._grace = timedelta(seconds=settings.sweeper_queued_grace_seconds)

    async def sweep(self) -> int:
        """Dead-letter exhausted jobs, then re-dispatch the recoverable ones."""
        now = func.now()
        pj = ProcessingJob
        async with self._sf() as session, session.begin():
            dead = await dead_letter_exhausted(session)
        async with self._sf() as session:
            rows = (
                await session.execute(
                    select(pj.id, pj.attempts)
                    .where(
                        or_(
                            and_(pj.status == JobStatus.QUEUED, pj.updated_at < now - self._grace),
                            and_(
                                pj.status == JobStatus.RETRY_SCHEDULED,
                                pj.next_attempt_at < now - self._grace,
                            ),
                            and_(pj.status == JobStatus.RUNNING, pj.lease_expires_at < now),
                        ),
                        pj.attempts < pj.max_attempts,
                    )
                    .order_by(pj.updated_at)
                    .limit(SWEEP_BATCH)
                )
            ).all()
        for job_id, attempts in rows:
            await self._scheduler.dispatch(job_id, token=attempts)
        if rows or dead:
            log.info("jobs.swept", count=len(rows), dead_lettered=len(dead))
        return len(rows) + len(dead)
