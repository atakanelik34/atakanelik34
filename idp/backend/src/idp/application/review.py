"""Human-in-the-loop review: the queue, field decisions, and resolving a paused job.

Every decision is recorded as a `ReviewAction` (original vs corrected value —
the feedback dataset) and audited. Validation is re-run after each change so
reviewers always see the current state. Approving resumes the *same* job from
its checkpoints; rejecting stops it; sending back starts a new job.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import AuditAction, AuditEntity, record_audit
from idp.application.document_status import change_document_status
from idp.application.jobs import JobScheduler, JobTrigger
from idp.application.results import PartResult, ResultService
from idp.application.validation import validate_part
from idp.domain.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from idp.domain.identity import Permission, Principal
from idp.domain.lifecycle import DocumentStatus, JobStatus, StepStatus
from idp.domain.normalization import normalize
from idp.domain.taxonomy import FieldType, SchemaDefinition
from idp.infrastructure.db.models import (
    Document,
    DocumentPage,
    DocumentPart,
    ExtractedField,
    ExtractionResult,
    ProcessingJob,
    ProcessingStep,
    ReviewAction,
    ReviewTask,
    SchemaVersion,
)
from idp.providers.extraction.base import new_row_id

OPEN = ("open", "in_progress")
HUMAN_CONFIDENCE = 1.0


@dataclass(frozen=True, slots=True)
class TaskSummary:
    task: ReviewTask
    document: Document


@dataclass(frozen=True, slots=True)
class TaskDetail:
    task: ReviewTask
    document: Document
    parts: list[PartResult]
    actions: Sequence[ReviewAction]


class ReviewService:
    def __init__(
        self, session: AsyncSession, *, scheduler: JobScheduler, retry_budget: int
    ) -> None:
        self._session = session
        self._scheduler = scheduler
        self._retry_budget = retry_budget

    # --- queries ------------------------------------------------------------------

    @staticmethod
    def _require(principal: Principal, permission: Permission) -> None:
        if not principal.has(permission):
            raise AuthorizationError(f"Missing permission '{permission.value}'")

    async def _task(
        self, principal: Principal, task_id: uuid.UUID, *, lock: bool = False
    ) -> ReviewTask:
        query = select(ReviewTask).where(
            ReviewTask.id == task_id, ReviewTask.tenant_id == principal.tenant_id
        )
        task = await self._session.scalar(query.with_for_update() if lock else query)
        if task is None:
            raise NotFoundError("Review task not found")
        return task

    async def list_tasks(
        self, principal: Principal, *, status: str | None, limit: int
    ) -> list[TaskSummary]:
        self._require(principal, Permission.REVIEWS_READ)
        query = (
            select(ReviewTask, Document)
            .join(Document, Document.id == ReviewTask.document_id)
            .where(ReviewTask.tenant_id == principal.tenant_id, Document.deleted_at.is_(None))
        )
        query = (
            query.where(ReviewTask.status == status)
            if status
            else query.where(ReviewTask.status.in_(OPEN))
        )
        rows = (
            await self._session.execute(query.order_by(ReviewTask.created_at).limit(limit))
        ).all()
        return [TaskSummary(task, document) for task, document in rows]

    async def counts(self, principal: Principal) -> dict[str, int]:
        self._require(principal, Permission.REVIEWS_READ)
        rows = (
            await self._session.execute(
                select(ReviewTask.status, func.count())
                .where(ReviewTask.tenant_id == principal.tenant_id)
                .group_by(ReviewTask.status)
            )
        ).all()
        return {str(status): int(count) for status, count in rows}

    async def detail(self, principal: Principal, task_id: uuid.UUID) -> TaskDetail:
        self._require(principal, Permission.REVIEWS_READ)
        task = await self._task(principal, task_id)
        document = await self._session.get(Document, task.document_id)
        if document is None:
            raise NotFoundError("Document not found")
        parts = await ResultService(self._session).part_results(task.job_id)
        actions = (
            await self._session.scalars(
                select(ReviewAction)
                .where(ReviewAction.task_id == task.id)
                .order_by(ReviewAction.created_at)
            )
        ).all()
        return TaskDetail(task=task, document=document, parts=parts, actions=actions)

    # --- helpers --------------------------------------------------------------------

    def _check_open(self, principal: Principal, task: ReviewTask) -> None:
        self._require(principal, Permission.REVIEWS_WRITE)
        if task.status not in OPEN:
            raise ConflictError(f"Review task is {task.status}", details={"status": task.status})
        if (
            task.assignee_id is not None
            and task.assignee_id != principal.user_id
            and not principal.has(Permission.USERS_WRITE)
        ):
            raise ConflictError("Review task is assigned to another reviewer")

    def _act(self, principal: Principal, task: ReviewTask, action: str, **values: Any) -> None:
        self._session.add(
            ReviewAction(
                tenant_id=task.tenant_id,
                task_id=task.id,
                actor_id=principal.user_id,
                action=action,
                **values,
            )
        )

    async def _revalidate(self, part: DocumentPart) -> None:
        pages = (
            await self._session.scalars(
                select(DocumentPage).where(DocumentPage.document_id == part.document_id)
            )
        ).all()
        await validate_part(self._session, part, pages)

    async def _field_in_task(
        self, task: ReviewTask, field_id: uuid.UUID
    ) -> tuple[ExtractedField, DocumentPart, SchemaDefinition]:
        row = (
            await self._session.execute(
                select(ExtractedField, DocumentPart, SchemaVersion)
                .join(ExtractionResult, ExtractionResult.id == ExtractedField.result_id)
                .join(DocumentPart, DocumentPart.id == ExtractedField.part_id)
                .join(SchemaVersion, SchemaVersion.id == ExtractionResult.schema_version_id)
                .where(ExtractedField.id == field_id, ExtractionResult.job_id == task.job_id)
                .with_for_update(of=ExtractedField)
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Field not found in this review")
        field, part, version = row
        return field, part, SchemaDefinition.model_validate(version.definition)

    # --- commands ---------------------------------------------------------------------

    async def claim(self, principal: Principal, task_id: uuid.UUID) -> ReviewTask:
        task = await self._task(principal, task_id, lock=True)
        self._check_open(principal, task)
        task.assignee_id = principal.user_id
        task.status = "in_progress"
        self._act(principal, task, "claim")
        record_audit(
            self._session,
            action=AuditAction.REVIEW_CLAIMED,
            entity_type=AuditEntity.REVIEW_TASK,
            entity_id=task.id,
            tenant_id=task.tenant_id,
            actor=principal,
        )
        await self._session.commit()
        return task

    async def field_action(
        self,
        principal: Principal,
        task_id: uuid.UUID,
        field_id: uuid.UUID,
        *,
        action: str,
        value: Any = None,
        reason: str | None = None,
    ) -> None:
        task = await self._task(principal, task_id, lock=True)
        self._check_open(principal, task)
        field, part, schema = await self._field_in_task(task, field_id)
        definition = schema.field(field.path)
        before = field.value
        now = datetime.now(UTC)
        if action == "accept":
            if field.status == "missing":
                raise ValidationError("A missing field cannot be accepted; enter a value instead")
            field.status, audit_action = "accepted", AuditAction.REVIEW_FIELD_ACCEPTED
        elif action == "edit":
            if definition is None or definition.type in (FieldType.ARRAY, FieldType.OBJECT):
                raise ValidationError("Only scalar fields can be edited")
            normalized = normalize(str(value) if value is not None else "", definition)
            if not normalized.ok:
                raise ValidationError(
                    f"Value is not a valid {definition.type.value}: {normalized.reason}",
                    details={"path": field.path, "reason": normalized.reason},
                )
            field.value, field.status, field.normalized = normalized.value, "corrected", True
            field.confidence = HUMAN_CONFIDENCE
            audit_action = AuditAction.REVIEW_FIELD_CORRECTED
        elif action == "reject":
            field.status, audit_action = "rejected", AuditAction.REVIEW_FIELD_REJECTED
        else:
            raise ValidationError("action must be accept, edit or reject")
        field.reviewed_by_id, field.reviewed_at = principal.user_id, now
        self._act(
            principal,
            task,
            action if action != "reject" else "reject_field",
            field_id=field.id,
            path=field.path,
            row_id=field.row_id or None,
            original_value=before,
            corrected_value=field.value,
            reason=reason,
        )
        record_audit(
            self._session,
            action=audit_action,
            entity_type=AuditEntity.EXTRACTED_FIELD,
            entity_id=field.id,
            tenant_id=task.tenant_id,
            actor=principal,
            before={"value": before},
            after={"value": field.value, "status": field.status},
        )
        await self._session.flush()
        await self._revalidate(part)
        await self._session.commit()

    async def add_row(
        self,
        principal: Principal,
        task_id: uuid.UUID,
        part_id: uuid.UUID,
        array_path: str,
        cells: dict[str, Any],
    ) -> str:
        task = await self._task(principal, task_id, lock=True)
        self._check_open(principal, task)
        result = await self._session.scalar(
            select(ExtractionResult).where(
                ExtractionResult.part_id == part_id, ExtractionResult.job_id == task.job_id
            )
        )
        if result is None:
            raise NotFoundError("Part not found in this review")
        version = await self._session.get(SchemaVersion, result.schema_version_id)
        if version is None:
            raise NotFoundError("Schema version not found")
        schema = SchemaDefinition.model_validate(version.definition)
        row_id = new_row_id()
        for child, raw in cells.items():
            path = f"{array_path}.{child}"
            definition = schema.field(path)
            if definition is None:
                raise ValidationError(f"Unknown column '{child}'")
            normalized = normalize(str(raw), definition)
            if not normalized.ok:
                raise ValidationError(f"{child}: {normalized.reason}")
            self._session.add(
                ExtractedField(
                    tenant_id=task.tenant_id,
                    result_id=result.id,
                    part_id=part_id,
                    path=path,
                    row_id=row_id,
                    value=normalized.value,
                    original_value=None,
                    raw_text=str(raw),
                    confidence=HUMAN_CONFIDENCE,
                    status="corrected",
                    normalized=True,
                    method="human",
                    provider="reviewer",
                    provenance={"method": "human", "reviewer_id": str(principal.user_id)},
                    reviewed_by_id=principal.user_id,
                    reviewed_at=datetime.now(UTC),
                )
            )
        self._act(principal, task, "add_row", path=array_path, row_id=row_id, corrected_value=cells)
        record_audit(
            self._session,
            action=AuditAction.REVIEW_ROW_ADDED,
            entity_type=AuditEntity.REVIEW_TASK,
            entity_id=task.id,
            tenant_id=task.tenant_id,
            actor=principal,
            after={"row_id": row_id, "path": array_path},
        )
        await self._session.flush()
        part = await self._session.get(DocumentPart, part_id)
        if part is None:
            raise NotFoundError("Part not found")
        await self._revalidate(part)
        await self._session.commit()
        return row_id

    async def delete_row(
        self, principal: Principal, task_id: uuid.UUID, part_id: uuid.UUID, row_id: str
    ) -> None:
        task = await self._task(principal, task_id, lock=True)
        self._check_open(principal, task)
        result_ids = select(ExtractionResult.id).where(ExtractionResult.job_id == task.job_id)
        changed = await self._session.execute(
            update(ExtractedField)
            .where(
                ExtractedField.part_id == part_id,
                ExtractedField.row_id == row_id,
                ExtractedField.result_id.in_(result_ids),
            )
            .values(
                status="rejected", reviewed_by_id=principal.user_id, reviewed_at=datetime.now(UTC)
            )
        )
        if not changed.rowcount:  # type: ignore[attr-defined]
            raise NotFoundError("Row not found in this review")
        self._act(principal, task, "delete_row", row_id=row_id)
        record_audit(
            self._session,
            action=AuditAction.REVIEW_ROW_DELETED,
            entity_type=AuditEntity.REVIEW_TASK,
            entity_id=task.id,
            tenant_id=task.tenant_id,
            actor=principal,
            after={"row_id": row_id},
        )
        part = await self._session.get(DocumentPart, part_id)
        if part is None:
            raise NotFoundError("Part not found")
        await self._revalidate(part)
        await self._session.commit()

    async def _resolve(
        self, principal: Principal, task: ReviewTask, status: str, note: str | None
    ) -> tuple[ProcessingJob, Document, ProcessingStep | None]:
        job = await self._session.get(ProcessingJob, task.job_id, with_for_update=True)
        document = await self._session.get(Document, task.document_id, with_for_update=True)
        if job is None or document is None:
            raise NotFoundError("Job not found")
        if job.status is not JobStatus.WAITING_FOR_REVIEW:
            raise ConflictError("The job is not waiting for review")
        step = await self._session.scalar(
            select(ProcessingStep).where(
                ProcessingStep.job_id == job.id, ProcessingStep.status == StepStatus.WAITING
            )
        )
        task.status, task.resolved_by_id, task.resolved_at = (
            status,
            principal.user_id,
            datetime.now(UTC),
        )
        task.resolution_note = note
        return job, document, step

    async def approve(self, principal: Principal, task_id: uuid.UUID, note: str | None) -> None:
        task = await self._task(principal, task_id, lock=True)
        self._check_open(principal, task)
        job, document, step = await self._resolve(principal, task, "approved", note)
        from idp.domain.validation import NEEDS_HUMAN  # noqa: PLC0415
        from idp.infrastructure.db.models import ValidationResult  # noqa: PLC0415

        overridden = (
            await self._session.scalars(
                select(ValidationResult.rule_id).where(
                    ValidationResult.job_id == job.id,
                    ValidationResult.outcome.in_([o.value for o in NEEDS_HUMAN]),
                )
            )
        ).all()
        if step is not None:
            step.status = StepStatus.SUCCEEDED
            step.finished_at = datetime.now(UTC)
            step.metrics = {"resolution": "approved", "overridden_rules": list(overridden)}
        # Resume the same run; it gets a fresh retry budget for its remaining steps.
        job.status, job.next_attempt_at, job.lease_expires_at = JobStatus.QUEUED, None, None
        job.max_attempts = job.attempts + self._retry_budget
        change_document_status(
            self._session,
            document,
            DocumentStatus.PROCESSING,
            reason="review approved",
            actor=principal,
        )
        self._act(
            principal,
            task,
            "approve",
            reason=note,
            corrected_value={"overridden_rules": list(overridden)},
        )
        record_audit(
            self._session,
            action=AuditAction.REVIEW_APPROVED,
            entity_type=AuditEntity.REVIEW_TASK,
            entity_id=task.id,
            tenant_id=task.tenant_id,
            actor=principal,
            after={"overridden_rules": list(overridden)},
        )
        await self._session.commit()
        await self._scheduler.dispatch(job.id, token=job.attempts)

    async def reject(self, principal: Principal, task_id: uuid.UUID, reason: str) -> None:
        task = await self._task(principal, task_id, lock=True)
        self._check_open(principal, task)
        job, document, step = await self._resolve(principal, task, "rejected", reason)
        self._stop(job, step, "rejected by reviewer")
        change_document_status(
            self._session, document, DocumentStatus.REJECTED, reason=reason, actor=principal
        )
        self._act(principal, task, "reject", reason=reason)
        record_audit(
            self._session,
            action=AuditAction.REVIEW_REJECTED,
            entity_type=AuditEntity.REVIEW_TASK,
            entity_id=task.id,
            tenant_id=task.tenant_id,
            actor=principal,
            after={"reason": reason},
        )
        await self._session.commit()

    async def send_back(
        self, principal: Principal, task_id: uuid.UUID, reason: str
    ) -> ProcessingJob:
        task = await self._task(principal, task_id, lock=True)
        self._check_open(principal, task)
        job, document, step = await self._resolve(principal, task, "sent_back", reason)
        self._stop(job, step, "sent back for reprocessing")
        await self._session.flush()
        change_document_status(
            self._session, document, DocumentStatus.QUEUED, reason=reason, actor=principal
        )
        new_job = self._scheduler.new_job(
            document, trigger=JobTrigger.REVIEW_SEND_BACK, requested_by=principal
        )
        self._session.add(new_job)
        self._act(principal, task, "send_back", reason=reason)
        record_audit(
            self._session,
            action=AuditAction.REVIEW_SENT_BACK,
            entity_type=AuditEntity.REVIEW_TASK,
            entity_id=task.id,
            tenant_id=task.tenant_id,
            actor=principal,
            after={"reason": reason, "new_job_id": str(new_job.id)},
        )
        await self._session.commit()
        await self._session.refresh(new_job)
        await self._scheduler.dispatch(new_job.id, token=0)
        return new_job

    @staticmethod
    def _stop(job: ProcessingJob, step: ProcessingStep | None, why: str) -> None:
        if step is not None:
            step.status = StepStatus.CANCELLED
            step.finished_at = datetime.now(UTC)
            step.error_message = why
        job.status, job.finished_at, job.lease_expires_at = (
            JobStatus.CANCELLED,
            datetime.now(UTC),
            None,
        )
