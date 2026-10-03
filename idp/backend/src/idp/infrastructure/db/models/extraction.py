from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from idp.infrastructure.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ExtractionResult(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Extraction output for one part in one job. Immutable history per job."""

    __tablename__ = "extraction_results"
    __table_args__ = (Index("uq_extraction_results_job_part", "job_id", "part_id", unique=True),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_parts.id", ondelete="CASCADE"), nullable=False
    )
    schema_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_versions.id", ondelete="RESTRICT"), nullable=False
    )
    route: Mapped[str] = mapped_column(String(64), nullable=False)
    providers: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    route_trace: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    cost_estimate: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")


class ExtractedField(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """One field value with confidence and provenance.

    Repeating groups use a stable `row_id` (never an array index); scalars use "".
    Human corrections update `value`/`status` and keep the machine value in
    `original_value`; every change is also an audited review action.
    """

    __tablename__ = "extracted_fields"
    __table_args__ = (
        Index("uq_extracted_fields_result_path_row", "result_id", "path", "row_id", unique=True),
        Index("ix_extracted_fields_part", "part_id"),
    )

    result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extraction_results.id", ondelete="CASCADE"), nullable=False
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_parts.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    row_id: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    original_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # extracted | missing | accepted | corrected | rejected
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    normalized: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    method: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(64))
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    alternatives: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
