from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from idp.infrastructure.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Machine credential. Only a SHA-256 of the high-entropy secret is stored."""

    __tablename__ = "api_keys"
    __table_args__ = (Index("uq_api_keys_prefix", "prefix", unique=True),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionRun(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """One configured business action for one part in one job. Idempotent by key."""

    __tablename__ = "action_runs"
    __table_args__ = (
        Index("uq_action_runs_idempotency_key", "idempotency_key", unique=True),
        Index("ix_action_runs_document", "document_id", "created_at"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_parts.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    connection_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # pending_approval | approved | rejected | succeeded | failed
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    response: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    external_reference: Mapped[str | None] = mapped_column(String(200))
    error_code: Mapped[str | None] = mapped_column(String(64))
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Domain event written in the same transaction as the change it describes."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index(
            "ix_outbox_events_pending",
            "next_attempt_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # connection id -> "ok" for subscribers already served (retries skip them)
    deliveries: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    last_error: Mapped[str | None] = mapped_column(String(200))
