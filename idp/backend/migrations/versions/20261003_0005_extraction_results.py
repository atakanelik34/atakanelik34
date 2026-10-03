"""extraction results

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-03 21:51:56.411532+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extraction_results",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("part_id", sa.UUID(), nullable=False),
        sa.Column("schema_version_id", sa.UUID(), nullable=False),
        sa.Column("route", sa.String(length=64), nullable=False),
        sa.Column("providers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "route_trace",
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
            "cost_estimate", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False
        ),
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
            name=op.f("fk_extraction_results_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["processing_jobs.id"],
            name=op.f("fk_extraction_results_job_id_processing_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["document_parts.id"],
            name=op.f("fk_extraction_results_part_id_document_parts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["schema_version_id"],
            ["schema_versions.id"],
            name=op.f("fk_extraction_results_schema_version_id_schema_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_extraction_results_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extraction_results")),
    )
    op.create_index(
        op.f("ix_extraction_results_document_id"),
        "extraction_results",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_extraction_results_tenant_id"), "extraction_results", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_extraction_results_job_part", "extraction_results", ["job_id", "part_id"], unique=True
    )
    op.create_table(
        "extracted_fields",
        sa.Column("result_id", sa.UUID(), nullable=False),
        sa.Column("part_id", sa.UUID(), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("row_id", sa.String(length=32), server_default="", nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("original_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("normalized", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "alternatives",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reviewed_by_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["part_id"],
            ["document_parts.id"],
            name=op.f("fk_extracted_fields_part_id_document_parts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["result_id"],
            ["extraction_results.id"],
            name=op.f("fk_extracted_fields_result_id_extraction_results"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
            name=op.f("fk_extracted_fields_reviewed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_extracted_fields_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_fields")),
    )
    op.create_index("ix_extracted_fields_part", "extracted_fields", ["part_id"], unique=False)
    op.create_index(
        op.f("ix_extracted_fields_tenant_id"), "extracted_fields", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_extracted_fields_result_path_row",
        "extracted_fields",
        ["result_id", "path", "row_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_extracted_fields_result_path_row", table_name="extracted_fields")
    op.drop_index(op.f("ix_extracted_fields_tenant_id"), table_name="extracted_fields")
    op.drop_index("ix_extracted_fields_part", table_name="extracted_fields")
    op.drop_table("extracted_fields")
    op.drop_index("uq_extraction_results_job_part", table_name="extraction_results")
    op.drop_index(op.f("ix_extraction_results_tenant_id"), table_name="extraction_results")
    op.drop_index(op.f("ix_extraction_results_document_id"), table_name="extraction_results")
    op.drop_table("extraction_results")
