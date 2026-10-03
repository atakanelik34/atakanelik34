from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from idp.infrastructure.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Connection(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """A configured external or internal data source (no secrets: env references only)."""

    __tablename__ = "connections"
    __table_args__ = (Index("uq_connections_tenant_key", "tenant_id", "key", unique=True),)

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # master_data | rest | mock_erp
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class MasterDataRecord(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """A reference record (vendor, customer, …) imported into a master-data connection."""

    __tablename__ = "master_data_records"
    __table_args__ = (
        Index(
            "uq_master_data_records_key",
            "connection_id",
            "entity",
            "record_key",
            unique=True,
        ),
        Index("ix_master_data_records_tax_id", "connection_id", "tax_id_norm"),
        Index("ix_master_data_records_iban", "connection_id", "iban_norm"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    record_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    name_norm: Mapped[str] = mapped_column(String(300), nullable=False)
    tax_id_norm: Mapped[str | None] = mapped_column(String(64))
    iban_norm: Mapped[str | None] = mapped_column(String(64))
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class EnrichmentResult(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    """Outcome of one enrichment rule for one part in one job."""

    __tablename__ = "enrichment_results"
    __table_args__ = (
        Index("uq_enrichment_results_job_part_name", "job_id", "part_id", "name", unique=True),
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
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    # matched | not_found | ambiguous | skipped | not_configured | error
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    record_key: Mapped[str | None] = mapped_column(String(128))
    score: Mapped[float | None] = mapped_column(Float)
    matched_on: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    criteria: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    outputs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
