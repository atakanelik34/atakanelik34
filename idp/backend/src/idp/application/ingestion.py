"""Ingestion: the single entry path for documents from every channel.

size limit → magic-byte type detection (declared type is advisory) → allow-list
→ malware scan → sha256 → duplicate check → object storage → Document + job
(one transaction, audited) → dispatch after commit.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from idp.application.audit import AuditAction, AuditEntity, record_audit
from idp.application.document_status import change_document_status
from idp.application.jobs import JobScheduler, JobTrigger
from idp.application.users import DEFAULT_PROJECT_KEY
from idp.domain.documents import (
    ACCEPTED_MIME_TYPES,
    SNIFF_BYTES,
    DocumentSource,
    ScanStatus,
    detect_mime_type,
    sanitize_filename,
)
from idp.domain.errors import (
    AuthorizationError,
    DocumentError,
    DuplicateDocumentError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from idp.domain.identity import Permission, Principal
from idp.domain.lifecycle import DocumentStatus
from idp.infrastructure.db.models import Document, ProcessingJob, Project
from idp.infrastructure.logging import get_logger
from idp.infrastructure.scanning import MalwareScanner
from idp.infrastructure.storage.base import ObjectStorageProvider, build_key

log = get_logger(__name__)

_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class IncomingFile:
    """A file from any channel. `stream` must be seekable (spooled temp file)."""

    stream: BinaryIO
    filename: str | None
    declared_mime_type: str | None
    source: DocumentSource
    project_key: str | None = None


@dataclass(frozen=True, slots=True)
class IngestResult:
    document: Document
    job: ProcessingJob
    dispatched: bool


@dataclass(frozen=True, slots=True)
class _Fingerprint:
    size: int
    sha256: str
    head: bytes


def _fingerprint(stream: BinaryIO, max_bytes: int) -> _Fingerprint:
    """Hash and measure a stream without loading it into memory."""
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    head = b""
    while chunk := stream.read(_CHUNK):
        if not head:
            head = chunk[:SNIFF_BYTES]
        size += len(chunk)
        if size > max_bytes:
            raise PayloadTooLargeError(f"File exceeds the {max_bytes // (1024 * 1024)} MiB limit")
        digest.update(chunk)
    stream.seek(0)
    return _Fingerprint(size=size, sha256=digest.hexdigest(), head=head)


class IngestionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: ObjectStorageProvider,
        scanner: MalwareScanner,
        scheduler: JobScheduler,
        max_upload_bytes: int,
    ) -> None:
        self._session = session
        self._storage = storage
        self._scanner = scanner
        self._scheduler = scheduler
        self._max_bytes = max_upload_bytes

    async def _resolve_project(self, principal: Principal, key: str | None) -> Project:
        project = await self._session.scalar(
            select(Project).where(
                Project.tenant_id == principal.tenant_id,
                Project.key == (key or DEFAULT_PROJECT_KEY),
                Project.deleted_at.is_(None),
            )
        )
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def _find_duplicate(
        self, principal: Principal, project: Project, sha256: str
    ) -> uuid.UUID | None:
        return await self._session.scalar(
            select(Document.id).where(
                Document.tenant_id == principal.tenant_id,
                Document.project_id == project.id,
                Document.sha256 == sha256,
                Document.deleted_at.is_(None),
            )
        )

    async def _reject_duplicate(self, principal: Principal, existing_id: uuid.UUID) -> None:
        record_audit(
            self._session,
            action=AuditAction.DOCUMENT_DUPLICATE_REJECTED,
            entity_type=AuditEntity.DOCUMENT,
            entity_id=existing_id,
            tenant_id=principal.tenant_id,
            actor=principal,
        )
        await self._session.commit()
        raise DuplicateDocumentError(
            "This file was already uploaded to the project",
            details={"existing_document_id": str(existing_id)},
        )

    async def ingest(self, principal: Principal, incoming: IncomingFile) -> IngestResult:
        if not principal.has(Permission.DOCUMENTS_WRITE):
            raise AuthorizationError("Not allowed to upload documents")
        project = await self._resolve_project(principal, incoming.project_key)

        fp = await asyncio.to_thread(_fingerprint, incoming.stream, self._max_bytes)
        if fp.size == 0:
            raise DocumentError("File is empty")
        mime_type = detect_mime_type(fp.head)
        if mime_type is None or mime_type not in ACCEPTED_MIME_TYPES:
            raise UnsupportedMediaTypeError(
                "Unsupported file type; accepted: PDF, PNG, JPEG, TIFF",
                details={"accepted": sorted(ACCEPTED_MIME_TYPES)},
            )

        verdict = await self._scanner.scan(incoming.stream)
        incoming.stream.seek(0)
        if verdict.status is ScanStatus.INFECTED:
            raise DocumentError("File rejected by malware scanner")

        existing = await self._find_duplicate(principal, project, fp.sha256)
        if existing is not None:
            await self._reject_duplicate(principal, existing)

        document_id = uuid.uuid4()
        storage_key = build_key(principal.tenant_id, "documents", str(document_id), "original")
        await self._storage.put(storage_key, incoming.stream, content_type=mime_type, size=fp.size)

        document = Document(
            id=document_id,
            tenant_id=principal.tenant_id,
            project_id=project.id,
            source=incoming.source.value,
            original_filename=sanitize_filename(incoming.filename),
            declared_mime_type=(incoming.declared_mime_type or None),
            detected_mime_type=mime_type,
            size_bytes=fp.size,
            sha256=fp.sha256,
            storage_key=storage_key,
            status=DocumentStatus.RECEIVED,
            scan_status=verdict.status.value,
            metadata_={"scanner": verdict.scanner},
            uploaded_by_id=principal.user_id,
            received_at=datetime.now(UTC),
        )
        self._session.add(document)
        try:
            await self._session.flush()
        except IntegrityError:
            # Lost a race with an identical concurrent upload.
            await self._session.rollback()
            await self._storage.delete(storage_key)
            existing = await self._find_duplicate(principal, project, fp.sha256)
            if existing is not None:
                await self._reject_duplicate(principal, existing)
            raise

        record_audit(
            self._session,
            action=AuditAction.DOCUMENT_RECEIVED,
            entity_type=AuditEntity.DOCUMENT,
            entity_id=document.id,
            tenant_id=principal.tenant_id,
            actor=principal,
            after={
                "filename": document.original_filename,
                "mime_type": mime_type,
                "size_bytes": fp.size,
                "sha256": fp.sha256,
                "source": incoming.source.value,
                "scan_status": verdict.status.value,
            },
        )
        job = self._scheduler.new_job(document, trigger=JobTrigger.UPLOAD, requested_by=principal)
        self._session.add(job)
        change_document_status(
            self._session,
            document,
            DocumentStatus.QUEUED,
            reason="queued for processing",
            actor=principal,
        )
        try:
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            await self._storage.delete(storage_key)
            raise
        # Load server-generated columns now; lazy loads are not possible in async code.
        await self._session.refresh(document)
        await self._session.refresh(job)

        dispatched = await self._scheduler.dispatch(job.id, token=0)
        log.info(
            "document.received",
            document_id=str(document.id),
            job_id=str(job.id),
            size_bytes=fp.size,
            mime_type=mime_type,
            dispatched=dispatched,
        )
        return IngestResult(document=document, job=job, dispatched=dispatched)
