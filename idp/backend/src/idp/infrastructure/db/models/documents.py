from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from idp.domain.lifecycle import ACTIVE_JOB_STATUSES, DocumentStatus, JobStatus, StepStatus
from idp.infrastructure.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


def _enum(enum_cls: type[Any], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


_ACTIVE_JOB_SQL = ", ".join(f"'{s.value}'" for s in sorted(ACTIVE_JOB_STATUSES))


class Document(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, TenantScopedMixin, Base):
    """A file as received. Bytes live in object storage under `storage_key`."""

    __tablename__ = "documents"
    __table_args__ = (
        # Duplicate guard: the same bytes once per project among live documents.
        Index(
            "uq_documents_tenant_project_sha256_live",
            "tenant_id",
            "project_id",
            "sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_documents_tenant_created", "tenant_id", "created_at", "id"),
        Index("ix_documents_tenant_status", "tenant_id", "status"),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus, "document_status"), nullable=False
    )
    scan_status: Mapped[str] = mapped_column(String(32), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentPage(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        Index("uq_document_pages_document_page", "document_id", "page_number", unique=True),
        CheckConstraint("page_number >= 1", name="page_number_positive"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[float] = mapped_column(Float, nullable=False)
    height: Mapped[float] = mapped_column(Float, nullable=False)
    # "pt" (PDF points, 1/72 in) or "px" (raster images).
    unit: Mapped[str] = mapped_column(String(8), nullable=False)
    rotation: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    has_text_layer: Mapped[bool] = mapped_column(Boolean, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class ProcessingJob(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """One execution of a workflow on a document (the workflow run)."""

    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index(
            "uq_processing_jobs_document_active",
            "document_id",
            unique=True,
            postgresql_where=text(f"status IN ({_ACTIVE_JOB_SQL})"),
        ),
        Index("ix_processing_jobs_document_created", "document_id", "created_at"),
        Index("ix_processing_jobs_status_next_attempt", "status", "next_attempt_at"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    workflow_key: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_step: Mapped[str | None] = mapped_column(String(64))
    last_error_category: Mapped[str | None] = mapped_column(String(32))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingStep(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One attempt of one workflow step within a job."""

    __tablename__ = "processing_steps"
    __table_args__ = (
        Index("uq_processing_steps_job_step_attempt", "job_id", "step_key", "attempt", unique=True),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus, "step_status"), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_version: Mapped[str | None] = mapped_column(String(32))
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error_category: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
