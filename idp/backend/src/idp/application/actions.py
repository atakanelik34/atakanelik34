"""Approving or rejecting a document's pending business actions.

Mirrors review approval: the waiting `approve_actions` step is marked
SUCCEEDED, the same job resumes with a fresh retry budget, and the `action`
step executes approved runs (rejected ones are skipped, so a fully rejected
set simply completes the document without side effects).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import AuditAction, AuditEntity, record_audit
from idp.application.document_status import change_document_status
from idp.application.jobs import JobScheduler
from idp.domain.errors import AuthorizationError, ConflictError, NotFoundError
from idp.domain.identity import Permission, Principal
from idp.domain.lifecycle import DocumentStatus, JobStatus, StepStatus
from idp.infrastructure.db.models import ActionRun, Document, ProcessingJob, ProcessingStep


class ActionService:
    def __init__(
        self, session: AsyncSession, *, scheduler: JobScheduler, retry_budget: int
    ) -> None:
        self._session = session
        self._scheduler = scheduler
        self._retry_budget = retry_budget

    async def _document(
        self, principal: Principal, document_id: uuid.UUID, *, lock: bool
    ) -> Document:
        document = await self._session.get(Document, document_id, with_for_update=lock)
        if document is None or document.tenant_id != principal.tenant_id or document.deleted_at:
            raise NotFoundError("Document not found")
        return document

    async def list_runs(self, principal: Principal, document_id: uuid.UUID) -> Sequence[ActionRun]:
        if not principal.has(Permission.DOCUMENTS_READ):
            raise AuthorizationError("Missing permission 'documents:read'")
        await self._document(principal, document_id, lock=False)
        return (
            await self._session.scalars(
                select(ActionRun)
                .where(ActionRun.document_id == document_id)
                .order_by(ActionRun.created_at.desc(), ActionRun.name)
            )
        ).all()

    async def decide(
        self, principal: Principal, document_id: uuid.UUID, *, approve: bool, note: str | None
    ) -> list[ActionRun]:
        if not principal.has(Permission.ACTIONS_EXECUTE):
            raise AuthorizationError("Missing permission 'actions:execute'")
        document = await self._document(principal, document_id, lock=True)
        if document.status is not DocumentStatus.READY_FOR_ACTION:
            raise ConflictError("The document has no actions awaiting approval")
        job = await self._session.scalar(
            select(ProcessingJob)
            .where(
                ProcessingJob.document_id == document.id,
                ProcessingJob.status == JobStatus.WAITING_FOR_REVIEW,
            )
            .with_for_update()
        )
        if job is None:
            raise ConflictError("No job is waiting for action approval")
        runs = list(
            (
                await self._session.scalars(
                    select(ActionRun).where(
                        ActionRun.job_id == job.id, ActionRun.status == "pending_approval"
                    )
                )
            ).all()
        )
        now = datetime.now(UTC)
        for run in runs:
            run.status = "approved" if approve else "rejected"
            run.decided_by_id, run.decided_at, run.decision_note = principal.user_id, now, note
        step = await self._session.scalar(
            select(ProcessingStep).where(
                ProcessingStep.job_id == job.id,
                ProcessingStep.step_key == "approve_actions",
                ProcessingStep.status == StepStatus.WAITING,
            )
        )
        if step is not None:
            step.status = StepStatus.SUCCEEDED
            step.finished_at = now
            step.metrics = {
                "resolution": "approved" if approve else "rejected",
                "actions": [r.name for r in runs],
            }
        job.status, job.next_attempt_at, job.lease_expires_at = JobStatus.QUEUED, None, None
        job.max_attempts = job.attempts + self._retry_budget
        change_document_status(
            self._session,
            document,
            DocumentStatus.PROCESSING,
            reason="actions approved" if approve else "actions rejected",
            actor=principal,
        )
        record_audit(
            self._session,
            action=AuditAction.ACTIONS_APPROVED if approve else AuditAction.ACTIONS_REJECTED,
            entity_type=AuditEntity.DOCUMENT,
            entity_id=document.id,
            tenant_id=document.tenant_id,
            actor=principal,
            after={"actions": [r.name for r in runs], "note": note},
        )
        await self._session.commit()
        await self._scheduler.dispatch(job.id, token=job.attempts)
        return runs
