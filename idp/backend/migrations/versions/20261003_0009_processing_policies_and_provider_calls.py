"""processing policies and provider calls

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-03 22:29:48.772933+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processing_policies",
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("allow_llm", sa.Boolean(), nullable=False),
        sa.Column("allow_mock_providers", sa.Boolean(), nullable=False),
        sa.Column("max_cost_per_document", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
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
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_processing_policies_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_processing_policies_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_policies")),
    )
    op.create_index(
        op.f("ix_processing_policies_tenant_id"), "processing_policies", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_processing_policies_tenant_version",
        "processing_policies",
        ["tenant_id", "version"],
        unique=True,
    )
    op.create_table(
        "provider_calls",
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("locality", sa.String(length=8), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
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
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_provider_calls_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["processing_jobs.id"],
            name=op.f("fk_provider_calls_job_id_processing_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_provider_calls_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_calls")),
    )
    op.create_index(
        "ix_provider_calls_tenant_created",
        "provider_calls",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_calls_tenant_id"), "provider_calls", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_provider_calls_tenant_id"), table_name="provider_calls")
    op.drop_index("ix_provider_calls_tenant_created", table_name="provider_calls")
    op.drop_table("provider_calls")
    op.drop_index("uq_processing_policies_tenant_version", table_name="processing_policies")
    op.drop_index(op.f("ix_processing_policies_tenant_id"), table_name="processing_policies")
    op.drop_table("processing_policies")
