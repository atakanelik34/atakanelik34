from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from idp.infrastructure.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class DocumentType(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, TenantScopedMixin, Base):
    __tablename__ = "document_types"
    __table_args__ = (
        Index(
            "uq_document_types_tenant_key_live",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    # Null project = available to every project of the tenant.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT")
    )
    key: Mapped[str] = mapped_column(String(63), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class SchemaVersion(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Immutable once published; edits happen on the single draft per type."""

    __tablename__ = "schema_versions"
    __table_args__ = (
        Index("uq_schema_versions_type_version", "document_type_id", "version", unique=True),
        Index(
            "uq_schema_versions_one_draft",
            "document_type_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
        Index(
            "uq_schema_versions_one_published",
            "document_type_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )

    document_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_types.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class SchemaField(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Flattened mirror of a published definition, for indexing and analytics."""

    __tablename__ = "schema_fields"
    __table_args__ = (
        Index("uq_schema_fields_version_path", "schema_version_id", "path", unique=True),
    )

    schema_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_versions.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence_threshold: Mapped[float] = mapped_column(Float, nullable=False)


class DocumentPart(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """A logical document inside a file, as classified by one job."""

    __tablename__ = "document_parts"
    __table_args__ = (
        Index("uq_document_parts_job_index", "job_id", "part_index", unique=True),
        Index("ix_document_parts_document", "document_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    part_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    document_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_types.id", ondelete="RESTRICT")
    )
    schema_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_versions.id", ondelete="RESTRICT")
    )
    classification_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    classifier: Mapped[str] = mapped_column(String(64), nullable=False)
    classification_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
