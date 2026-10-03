"""validation and review tasks

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-03 21:59:00.025184+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New enum values must be committed before any statement may use them.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'WAITING_FOR_REVIEW'")
        op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'CANCELLED'")
        op.execute("ALTER TYPE step_status ADD VALUE IF NOT EXISTS 'WAITING'")
        op.execute("ALTER TYPE step_status ADD VALUE IF NOT EXISTS 'CANCELLED'")
    # A job paused for review is still the document's active job.
    op.drop_index("uq_processing_jobs_document_active", table_name="processing_jobs")
    op.create_index(
        "uq_processing_jobs_document_active",
        "processing_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('QUEUED', 'RETRY_SCHEDULED', 'RUNNING', 'WAITING_FOR_REVIEW')"
        ),
    )
    op.create_table(
        "review_tasks",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assignee_id", sa.UUID(), nullable=True),
        sa.Column("resolved_by_id", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_review_tasks_assignee_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_review_tasks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["processing_jobs.id"],
            name=op.f("fk_review_tasks_job_id_processing_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"],
            ["users.id"],
            name=op.f("fk_review_tasks_resolved_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_review_tasks_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_tasks")),
    )
    op.create_index(op.f("ix_review_tasks_tenant_id"), "review_tasks", ["tenant_id"], unique=False)
    op.create_index(
        "ix_review_tasks_tenant_status",
        "review_tasks",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_review_tasks_job_open",
        "review_tasks",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'in_progress')"),
    )
    op.create_table(
        "validation_results",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("part_id", sa.UUID(), nullable=False),
        sa.Column("rule_id", sa.String(length=160), nullable=False),
        sa.Column("rule_type", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("field_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("message", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_validation_results_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["processing_jobs.id"],
            name=op.f("fk_validation_results_job_id_processing_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["document_parts.id"],
            name=op.f("fk_validation_results_part_id_document_parts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_validation_results_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validation_results")),
    )
    op.create_index(
        "ix_validation_results_job_part", "validation_results", ["job_id", "part_id"], unique=False
    )
    op.create_index(
        op.f("ix_validation_results_tenant_id"), "validation_results", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("uq_processing_jobs_document_active", table_name="processing_jobs")
    op.execute(
        "UPDATE processing_jobs SET status = 'FAILED' "
        "WHERE status IN ('WAITING_FOR_REVIEW', 'CANCELLED')"
    )
    op.execute(
        "UPDATE processing_steps SET status = 'FAILED' WHERE status IN ('WAITING', 'CANCELLED')"
    )
    for table, column, enum, values in (
        (
            "processing_jobs",
            "status",
            "job_status",
            "'QUEUED', 'RUNNING', 'RETRY_SCHEDULED', 'SUCCEEDED', 'FAILED', 'DEAD_LETTERED'",
        ),
        ("processing_steps", "status", "step_status", "'RUNNING', 'SUCCEEDED', 'FAILED'"),
    ):
        op.execute(f"ALTER TYPE {enum} RENAME TO {enum}_old")
        op.execute(f"CREATE TYPE {enum} AS ENUM ({values})")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {enum} USING {column}::text::{enum}"
        )
        op.execute(f"DROP TYPE {enum}_old")
    op.create_index(
        "uq_processing_jobs_document_active",
        "processing_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RETRY_SCHEDULED', 'RUNNING')"),
    )
    op.drop_index(op.f("ix_validation_results_tenant_id"), table_name="validation_results")
    op.drop_index("ix_validation_results_job_part", table_name="validation_results")
    op.drop_table("validation_results")
    op.drop_index(
        "uq_review_tasks_job_open",
        table_name="review_tasks",
        postgresql_where=sa.text("status IN ('open', 'in_progress')"),
    )
    op.drop_index("ix_review_tasks_tenant_status", table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_tenant_id"), table_name="review_tasks")
    op.drop_table("review_tasks")
