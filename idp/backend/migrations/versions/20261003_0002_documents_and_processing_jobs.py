"""documents and processing jobs

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03 20:44:21.583826+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("declared_mime_type", sa.String(length=255), nullable=True),
        sa.Column("detected_mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                "QUEUED",
                "PROCESSING",
                "WAITING_FOR_HUMAN",
                "READY_FOR_ACTION",
                "COMPLETED",
                "FAILED",
                "REJECTED",
                name="document_status",
            ),
            nullable=False,
        ),
        sa.Column("scan_status", sa.String(length=32), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("uploaded_by_id", sa.UUID(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_documents_size_non_negative")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_documents_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_documents_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"],
            ["users.id"],
            name=op.f("fk_documents_uploaded_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(
        "ix_documents_tenant_created", "documents", ["tenant_id", "created_at", "id"], unique=False
    )
    op.create_index(op.f("ix_documents_tenant_id"), "documents", ["tenant_id"], unique=False)
    op.create_index(
        "ix_documents_tenant_status", "documents", ["tenant_id", "status"], unique=False
    )
    op.create_index(
        "uq_documents_tenant_project_sha256_live",
        "documents",
        ["tenant_id", "project_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "document_pages",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("width", sa.Float(), nullable=False),
        sa.Column("height", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("rotation", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("has_text_layer", sa.Boolean(), nullable=False),
        sa.Column("char_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("page_number >= 1", name=op.f("ck_document_pages_page_number_positive")),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_pages_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_document_pages_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_pages")),
    )
    op.create_index(
        op.f("ix_document_pages_tenant_id"), "document_pages", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_document_pages_document_page",
        "document_pages",
        ["document_id", "page_number"],
        unique=True,
    )
    op.create_table(
        "processing_jobs",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("workflow_key", sa.String(length=64), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("pipeline_version", sa.String(length=32), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "RUNNING",
                "RETRY_SCHEDULED",
                "SUCCEEDED",
                "FAILED",
                "DEAD_LETTERED",
                name="job_status",
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_step", sa.String(length=64), nullable=True),
        sa.Column("last_error_category", sa.String(length=32), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("requested_by_id", sa.UUID(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_processing_jobs_attempts_non_negative")),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_processing_jobs_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_processing_jobs_requested_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_processing_jobs_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_jobs")),
    )
    op.create_index(
        "ix_processing_jobs_document_created",
        "processing_jobs",
        ["document_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_processing_jobs_status_next_attempt",
        "processing_jobs",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_processing_jobs_tenant_id"), "processing_jobs", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_processing_jobs_document_active",
        "processing_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RETRY_SCHEDULED', 'RUNNING')"),
    )
    op.create_table(
        "processing_steps",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("step_key", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.Enum("RUNNING", "SUCCEEDED", "FAILED", name="step_status"), nullable=False
        ),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("provider_version", sa.String(length=32), nullable=True),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_category", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["processing_jobs.id"],
            name=op.f("fk_processing_steps_job_id_processing_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_processing_steps_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_steps")),
    )
    op.create_index(
        op.f("ix_processing_steps_tenant_id"), "processing_steps", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_processing_steps_job_step_attempt",
        "processing_steps",
        ["job_id", "step_key", "attempt"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_processing_steps_job_step_attempt", table_name="processing_steps")
    op.drop_index(op.f("ix_processing_steps_tenant_id"), table_name="processing_steps")
    op.drop_table("processing_steps")
    op.drop_index(
        "uq_processing_jobs_document_active",
        table_name="processing_jobs",
        postgresql_where=sa.text("status IN ('QUEUED', 'RETRY_SCHEDULED', 'RUNNING')"),
    )
    op.drop_index(op.f("ix_processing_jobs_tenant_id"), table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_status_next_attempt", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_document_created", table_name="processing_jobs")
    op.drop_table("processing_jobs")
    op.drop_index("uq_document_pages_document_page", table_name="document_pages")
    op.drop_index(op.f("ix_document_pages_tenant_id"), table_name="document_pages")
    op.drop_table("document_pages")
    op.drop_index(
        "uq_documents_tenant_project_sha256_live",
        table_name="documents",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_documents_tenant_status", table_name="documents")
    op.drop_index(op.f("ix_documents_tenant_id"), table_name="documents")
    op.drop_index("ix_documents_tenant_created", table_name="documents")
    op.drop_table("documents")
    for enum_name in ("step_status", "job_status", "document_status"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
