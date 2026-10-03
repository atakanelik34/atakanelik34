from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from idp.infrastructure.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ProcessingPolicy(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """A tenant's processing policy. Append-only: every change is a new version."""

    __tablename__ = "processing_policies"
    __table_args__ = (
        Index("uq_processing_policies_tenant_version", "tenant_id", "version", unique=True),
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    allow_llm: Mapped[bool] = mapped_column(Boolean, nullable=False)
    allow_mock_providers: Mapped[bool] = mapped_column(Boolean, nullable=False)
    max_cost_per_document: Mapped[float | None] = mapped_column(Numeric(12, 6))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class ProviderCall(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """One model call (or refusal): usage and cost, never prompt or response content."""

    __tablename__ = "provider_calls"
    __table_args__ = (Index("ix_provider_calls_tenant_created", "tenant_id", "created_at"),)

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    locality: Mapped[str] = mapped_column(String(8), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64))
