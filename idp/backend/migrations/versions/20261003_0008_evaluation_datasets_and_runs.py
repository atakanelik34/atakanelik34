"""evaluation datasets and runs

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-03 22:18:49.858941+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_datasets",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("document_type_id", sa.UUID(), nullable=True),
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
            name=op.f("fk_evaluation_datasets_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_types.id"],
            name=op.f("fk_evaluation_datasets_document_type_id_document_types"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_evaluation_datasets_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_datasets")),
    )
    op.create_index(
        op.f("ix_evaluation_datasets_tenant_id"), "evaluation_datasets", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_evaluation_datasets_tenant_name",
        "evaluation_datasets",
        ["tenant_id", "name"],
        unique=True,
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("dataset_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "field_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "item_results",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
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
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_evaluation_runs_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["evaluation_datasets.id"],
            name=op.f("fk_evaluation_runs_dataset_id_evaluation_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_evaluation_runs_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
    )
    op.create_index(
        "ix_evaluation_runs_dataset", "evaluation_runs", ["dataset_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_evaluation_runs_tenant_id"), "evaluation_runs", ["tenant_id"], unique=False
    )
    op.create_table(
        "evaluation_items",
        sa.Column("dataset_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("document_type_key", sa.String(length=64), nullable=False),
        sa.Column("ground_truth", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_review_task_id", sa.UUID(), nullable=True),
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
            ["dataset_id"],
            ["evaluation_datasets.id"],
            name=op.f("fk_evaluation_items_dataset_id_evaluation_datasets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_evaluation_items_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_review_task_id"],
            ["review_tasks.id"],
            name=op.f("fk_evaluation_items_source_review_task_id_review_tasks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_evaluation_items_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_items")),
    )
    op.create_index(
        op.f("ix_evaluation_items_dataset_id"), "evaluation_items", ["dataset_id"], unique=False
    )
    op.create_index(
        op.f("ix_evaluation_items_tenant_id"), "evaluation_items", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_evaluation_items_dataset_part",
        "evaluation_items",
        ["dataset_id", "document_id", "page_start"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_evaluation_items_dataset_part", table_name="evaluation_items")
    op.drop_index(op.f("ix_evaluation_items_tenant_id"), table_name="evaluation_items")
    op.drop_index(op.f("ix_evaluation_items_dataset_id"), table_name="evaluation_items")
    op.drop_table("evaluation_items")
    op.drop_index(op.f("ix_evaluation_runs_tenant_id"), table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_dataset", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("uq_evaluation_datasets_tenant_name", table_name="evaluation_datasets")
    op.drop_index(op.f("ix_evaluation_datasets_tenant_id"), table_name="evaluation_datasets")
    op.drop_table("evaluation_datasets")
