from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from idp.infrastructure.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ValidationResult(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Current validation state of a part in a job (re-evaluated after corrections)."""

    __tablename__ = "validation_results"
    __table_args__ = (Index("ix_validation_results_job_part", "job_id", "part_id"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_parts.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(String(160), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    field_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ReviewTask(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """A human decision a paused job is waiting for. Not an error state."""

    __tablename__ = "review_tasks"
    __table_args__ = (
        Index(
            "uq_review_tasks_job_open",
            "job_id",
            unique=True,
            postgresql_where=text("status IN ('open', 'in_progress')"),
        ),
        Index("ix_review_tasks_tenant_status", "tenant_id", "status", "created_at"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    # open | in_progress | approved | rejected | sent_back | cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)


class ReviewAction(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Every reviewer decision; doubles as the feedback dataset (original vs corrected)."""

    __tablename__ = "review_actions"
    __table_args__ = (Index("ix_review_actions_task", "task_id", "created_at"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # claim | accept | edit | reject_field | add_row | delete_row | approve | reject | send_back
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    field_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extracted_fields.id", ondelete="SET NULL")
    )
    path: Mapped[str | None] = mapped_column(String(255))
    row_id: Mapped[str | None] = mapped_column(String(32))
    original_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    corrected_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
