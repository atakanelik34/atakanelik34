"""connections and enrichment

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-03 22:40:16.795793+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            name=op.f("fk_connections_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_connections_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connections")),
    )
    op.create_index(op.f("ix_connections_tenant_id"), "connections", ["tenant_id"], unique=False)
    op.create_index("uq_connections_tenant_key", "connections", ["tenant_id", "key"], unique=True)
    op.create_table(
        "master_data_records",
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("entity", sa.String(length=64), nullable=False),
        sa.Column("record_key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("name_norm", sa.String(length=300), nullable=False),
        sa.Column("tax_id_norm", sa.String(length=64), nullable=True),
        sa.Column("iban_norm", sa.String(length=64), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
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
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_master_data_records_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_master_data_records_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_data_records")),
    )
    op.create_index(
        "ix_master_data_records_iban",
        "master_data_records",
        ["connection_id", "iban_norm"],
        unique=False,
    )
    op.create_index(
        "ix_master_data_records_tax_id",
        "master_data_records",
        ["connection_id", "tax_id_norm"],
        unique=False,
    )
    op.create_index(
        op.f("ix_master_data_records_tenant_id"), "master_data_records", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_master_data_records_key",
        "master_data_records",
        ["connection_id", "entity", "record_key"],
        unique=True,
    )
    op.create_table(
        "enrichment_results",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("part_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("connection_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("record_key", sa.String(length=128), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "matched_on",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "criteria",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "outputs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("message", sa.Text(), server_default="", nullable=False),
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
            name=op.f("fk_enrichment_results_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["processing_jobs.id"],
            name=op.f("fk_enrichment_results_job_id_processing_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["document_parts.id"],
            name=op.f("fk_enrichment_results_part_id_document_parts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_enrichment_results_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrichment_results")),
    )
    op.create_index(
        op.f("ix_enrichment_results_tenant_id"), "enrichment_results", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_enrichment_results_job_part_name",
        "enrichment_results",
        ["job_id", "part_id", "name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_enrichment_results_job_part_name", table_name="enrichment_results")
    op.drop_index(op.f("ix_enrichment_results_tenant_id"), table_name="enrichment_results")
    op.drop_table("enrichment_results")
    op.drop_index("uq_master_data_records_key", table_name="master_data_records")
    op.drop_index(op.f("ix_master_data_records_tenant_id"), table_name="master_data_records")
    op.drop_index("ix_master_data_records_tax_id", table_name="master_data_records")
    op.drop_index("ix_master_data_records_iban", table_name="master_data_records")
    op.drop_table("master_data_records")
    op.drop_index("uq_connections_tenant_key", table_name="connections")
    op.drop_index(op.f("ix_connections_tenant_id"), table_name="connections")
    op.drop_table("connections")
