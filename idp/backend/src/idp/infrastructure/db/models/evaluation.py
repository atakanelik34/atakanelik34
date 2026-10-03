from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from idp.infrastructure.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class EvaluationDataset(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """A named set of documents with ground truth, used to measure the pipeline."""

    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        Index("uq_evaluation_datasets_tenant_name", "tenant_id", "name", unique=True),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    document_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_types.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class EvaluationItem(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Ground truth for one document part (page range) in a dataset."""

    __tablename__ = "evaluation_items"
    __table_args__ = (
        Index(
            "uq_evaluation_items_dataset_part",
            "dataset_id",
            "document_id",
            "page_start",
            unique=True,
        ),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    document_type_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # {"fields": {path: value}, "tables": {"lines[]": [{cell: value}, ...]}}
    ground_truth: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # review | manual
    source_review_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_tasks.id", ondelete="SET NULL")
    )


class EvaluationRun(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Metrics of the latest machine output for every item, frozen at run time."""

    __tablename__ = "evaluation_runs"
    __table_args__ = (Index("ix_evaluation_runs_dataset", "dataset_id", "created_at"),)

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # What was measured: pipeline/workflow/routing versions seen across items.
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    field_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    item_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
