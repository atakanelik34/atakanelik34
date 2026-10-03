"""Document queries and commands (list, detail, timeline, download, reprocess, delete).

Every lookup is tenant-scoped; another tenant's document is indistinguishable
from a missing one (404).
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import AuditAction, AuditEntity, record_audit
from idp.application.document_status import change_document_status
from idp.application.jobs import JobScheduler, JobTrigger
from idp.domain.errors import ConflictError, NotFoundError, ValidationError
from idp.domain.geometry import PageLayout
from idp.domain.identity import Principal
from idp.domain.lifecycle import (
    ACTIVE_JOB_STATUSES,
    REPROCESSABLE,
    DocumentStatus,
)
from idp.infrastructure.db.models import (
    AuditLog,
    Document,
    DocumentPage,
    DocumentPart,
    DocumentType,
    ProcessingJob,
    ProcessingStep,
    SchemaVersion,
)
from idp.infrastructure.layout_store import load_layout
from idp.infrastructure.storage.base import ObjectStorageProvider

MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class DocumentListPage:
    items: Sequence[Document]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class JobWithSteps:
    job: ProcessingJob
    steps: Sequence[ProcessingStep]


@dataclass(frozen=True, slots=True)
class Timeline:
    jobs: Sequence[JobWithSteps]
    status_changes: Sequence[AuditLog]


@dataclass(frozen=True, slots=True)
class DownloadLink:
    url: str
    expires_at: datetime


def encode_cursor(created_at: datetime, document_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(document_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_at, document_id = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(created_at), uuid.UUID(document_id)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Invalid cursor") from exc


class DocumentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: ObjectStorageProvider,
        scheduler: JobScheduler,
        signed_url_ttl_seconds: int,
    ) -> None:
        self._session = session
        self._storage = storage
        self._scheduler = scheduler
        self._ttl = signed_url_ttl_seconds

    async def get(
        self, principal: Principal, document_id: uuid.UUID, *, lock: bool = False
    ) -> Document:
        query = select(Document).where(
            Document.id == document_id,
            Document.tenant_id == principal.tenant_id,
            Document.deleted_at.is_(None),
        )
        if lock:
            query = query.with_for_update()
        document = await self._session.scalar(query)
        if document is None:
            raise NotFoundError("Document not found")
        return document

    async def list(
        self,
        principal: Principal,
        *,
        status: DocumentStatus | None,
        limit: int,
        cursor: str | None,
    ) -> DocumentListPage:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        query = select(Document).where(
            Document.tenant_id == principal.tenant_id, Document.deleted_at.is_(None)
        )
        if status is not None:
            query = query.where(Document.status == status)
        if cursor:
            created_at, last_id = decode_cursor(cursor)
            query = query.where(
                or_(
                    Document.created_at < created_at,
                    and_(Document.created_at == created_at, Document.id < last_id),
                )
            )
        rows = (
            await self._session.scalars(
                query.order_by(Document.created_at.desc(), Document.id.desc()).limit(limit + 1)
            )
        ).all()
        items = rows[:limit]
        next_cursor = (
            encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit else None
        )
        return DocumentListPage(items=items, next_cursor=next_cursor)

    async def pages(self, document: Document) -> Sequence[DocumentPage]:
        return (
            await self._session.scalars(
                select(DocumentPage)
                .where(DocumentPage.document_id == document.id)
                .order_by(DocumentPage.page_number)
            )
        ).all()

    async def timeline(self, principal: Principal, document_id: uuid.UUID) -> Timeline:
        document = await self.get(principal, document_id)
        jobs = (
            await self._session.scalars(
                select(ProcessingJob)
                .where(ProcessingJob.document_id == document.id)
                .order_by(ProcessingJob.created_at.desc())
            )
        ).all()
        steps = (
            await self._session.scalars(
                select(ProcessingStep)
                .where(ProcessingStep.job_id.in_([j.id for j in jobs]))
                .order_by(ProcessingStep.started_at, ProcessingStep.attempt)
            )
        ).all()
        by_job: dict[uuid.UUID, list[ProcessingStep]] = {}
        for step in steps:
            by_job.setdefault(step.job_id, []).append(step)
        changes = (
            await self._session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.tenant_id == principal.tenant_id,
                    AuditLog.entity_type == AuditEntity.DOCUMENT.value,
                    AuditLog.entity_id == str(document.id),
                    AuditLog.action == AuditAction.DOCUMENT_STATUS_CHANGED.value,
                )
                .order_by(AuditLog.occurred_at, AuditLog.id)
            )
        ).all()
        return Timeline(
            jobs=[JobWithSteps(job=j, steps=by_job.get(j.id, [])) for j in jobs],
            status_changes=changes,
        )

    async def page(self, principal: Principal, document_id: uuid.UUID, number: int) -> DocumentPage:
        document = await self.get(principal, document_id)
        page = await self._session.scalar(
            select(DocumentPage).where(
                DocumentPage.document_id == document.id, DocumentPage.page_number == number
            )
        )
        if page is None:
            raise NotFoundError("Page not found")
        return page

    async def page_image(
        self, principal: Principal, document_id: uuid.UUID, number: int
    ) -> tuple[DocumentPage, DownloadLink]:
        page = await self.page(principal, document_id, number)
        if page.image_key is None:
            raise NotFoundError("Page has not been digitized yet")
        url = await self._storage.signed_url(page.image_key, expires_in=self._ttl)
        return page, DownloadLink(
            url=url, expires_at=datetime.now(UTC) + timedelta(seconds=self._ttl)
        )

    async def page_layout(
        self, principal: Principal, document_id: uuid.UUID, number: int
    ) -> PageLayout:
        page = await self.page(principal, document_id, number)
        if page.layout_key is None:
            raise NotFoundError("Page has not been digitized yet")
        return await load_layout(self._storage, page.layout_key)

    async def latest_job(self, document: Document) -> ProcessingJob | None:
        return await self._session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.document_id == document.id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )

    async def parts(
        self, principal: Principal, document_id: uuid.UUID, job_id: uuid.UUID | None = None
    ) -> Sequence[tuple[DocumentPart, DocumentType | None, SchemaVersion | None]]:
        """Parts of the given job, or of the most recent job that produced parts."""
        document = await self.get(principal, document_id)
        if job_id is None:
            job_id = await self._session.scalar(
                select(DocumentPart.job_id)
                .join(ProcessingJob, ProcessingJob.id == DocumentPart.job_id)
                .where(DocumentPart.document_id == document.id)
                .order_by(ProcessingJob.created_at.desc())
                .limit(1)
            )
            if job_id is None:
                return []
        rows = (
            await self._session.execute(
                select(DocumentPart, DocumentType, SchemaVersion)
                .outerjoin(DocumentType, DocumentType.id == DocumentPart.document_type_id)
                .outerjoin(SchemaVersion, SchemaVersion.id == DocumentPart.schema_version_id)
                .where(DocumentPart.document_id == document.id, DocumentPart.job_id == job_id)
                .order_by(DocumentPart.part_index)
            )
        ).all()
        return [(part, doc_type, version) for part, doc_type, version in rows]

    async def download_link(self, principal: Principal, document_id: uuid.UUID) -> DownloadLink:
        document = await self.get(principal, document_id)
        url = await self._storage.signed_url(
            document.storage_key, expires_in=self._ttl, filename=document.original_filename
        )
        record_audit(
            self._session,
            action=AuditAction.DOCUMENT_DOWNLOADED,
            entity_type=AuditEntity.DOCUMENT,
            entity_id=document.id,
            tenant_id=principal.tenant_id,
            actor=principal,
        )
        await self._session.commit()
        return DownloadLink(url=url, expires_at=datetime.now(UTC) + timedelta(seconds=self._ttl))

    async def _has_active_job(self, document: Document) -> bool:
        active = await self._session.scalar(
            select(ProcessingJob.id).where(
                ProcessingJob.document_id == document.id,
                ProcessingJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        return active is not None

    async def request_processing(
        self, principal: Principal, document_id: uuid.UUID
    ) -> ProcessingJob:
        """Reprocess (or replay a failed document) as a new job; history is kept."""
        document = await self.get(principal, document_id, lock=True)
        if document.status not in REPROCESSABLE:
            raise ConflictError(
                f"Document in status {document.status.value} cannot be reprocessed",
                details={"status": document.status.value},
            )
        if document.status is DocumentStatus.WAITING_FOR_HUMAN:
            raise ConflictError(
                "Document is waiting for review; approve, reject or send back its review task"
            )
        if await self._has_active_job(document):
            raise ConflictError("Document already has an active processing job")

        trigger = (
            JobTrigger.REPLAY if document.status is DocumentStatus.FAILED else JobTrigger.REPROCESS
        )
        job = self._scheduler.new_job(document, trigger=trigger, requested_by=principal)
        self._session.add(job)
        change_document_status(
            self._session, document, DocumentStatus.QUEUED, reason=trigger.value, actor=principal
        )
        record_audit(
            self._session,
            action=AuditAction.DOCUMENT_PROCESSING_REQUESTED,
            entity_type=AuditEntity.DOCUMENT,
            entity_id=document.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after={"job_id": str(job.id), "trigger": trigger.value},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:  # concurrent request created the active job first
            await self._session.rollback()
            raise ConflictError("Document already has an active processing job") from exc
        await self._session.refresh(job)
        await self._scheduler.dispatch(job.id, token=0)
        return job

    async def delete(self, principal: Principal, document_id: uuid.UUID) -> None:
        """Soft delete. The original stays in storage until retention policy removes it."""
        document = await self.get(principal, document_id, lock=True)
        if await self._has_active_job(document):
            raise ConflictError("Document is being processed; try again when it finishes")
        document.deleted_at = datetime.now(UTC)
        record_audit(
            self._session,
            action=AuditAction.DOCUMENT_DELETED,
            entity_type=AuditEntity.DOCUMENT,
            entity_id=document.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            before={"status": document.status.value},
        )
        await self._session.commit()
