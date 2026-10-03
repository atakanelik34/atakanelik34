"""taxonomy and document parts

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03 21:41:40.515592+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_types",
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("key", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_document_types_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_document_types_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_document_types_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_types")),
    )
    op.create_index(
        op.f("ix_document_types_tenant_id"), "document_types", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_document_types_tenant_key_live",
        "document_types",
        ["tenant_id", "key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "schema_versions",
        sa.Column("document_type_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_id", sa.UUID(), nullable=True),
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
            name=op.f("fk_schema_versions_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_types.id"],
            name=op.f("fk_schema_versions_document_type_id_document_types"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_id"],
            ["users.id"],
            name=op.f("fk_schema_versions_published_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_schema_versions_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schema_versions")),
    )
    op.create_index(
        op.f("ix_schema_versions_tenant_id"), "schema_versions", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_schema_versions_one_draft",
        "schema_versions",
        ["document_type_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.create_index(
        "uq_schema_versions_one_published",
        "schema_versions",
        ["document_type_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        "uq_schema_versions_type_version",
        "schema_versions",
        ["document_type_id", "version"],
        unique=True,
    )
    op.create_table(
        "document_parts",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("document_type_id", sa.UUID(), nullable=True),
        sa.Column("schema_version_id", sa.UUID(), nullable=True),
        sa.Column("classification_confidence", sa.Float(), nullable=False),
        sa.Column("classifier", sa.String(length=64), nullable=False),
        sa.Column(
            "classification_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
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
            name=op.f("fk_document_parts_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_types.id"],
            name=op.f("fk_document_parts_document_type_id_document_types"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["processing_jobs.id"],
            name=op.f("fk_document_parts_job_id_processing_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["schema_version_id"],
            ["schema_versions.id"],
            name=op.f("fk_document_parts_schema_version_id_schema_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_document_parts_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_parts")),
    )
    op.create_index("ix_document_parts_document", "document_parts", ["document_id"], unique=False)
    op.create_index(
        op.f("ix_document_parts_tenant_id"), "document_parts", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_document_parts_job_index", "document_parts", ["job_id", "part_index"], unique=True
    )
    op.create_table(
        "schema_fields",
        sa.Column("schema_version_id", sa.UUID(), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("confidence_threshold", sa.Float(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["schema_version_id"],
            ["schema_versions.id"],
            name=op.f("fk_schema_fields_schema_version_id_schema_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_schema_fields_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schema_fields")),
    )
    op.create_index(
        op.f("ix_schema_fields_tenant_id"), "schema_fields", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_schema_fields_version_path", "schema_fields", ["schema_version_id", "path"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_schema_fields_version_path", table_name="schema_fields")
    op.drop_index(op.f("ix_schema_fields_tenant_id"), table_name="schema_fields")
    op.drop_table("schema_fields")
    op.drop_index("uq_document_parts_job_index", table_name="document_parts")
    op.drop_index(op.f("ix_document_parts_tenant_id"), table_name="document_parts")
    op.drop_index("ix_document_parts_document", table_name="document_parts")
    op.drop_table("document_parts")
    op.drop_index("uq_schema_versions_type_version", table_name="schema_versions")
    op.drop_index(
        "uq_schema_versions_one_published",
        table_name="schema_versions",
        postgresql_where=sa.text("status = 'published'"),
    )
    op.drop_index(
        "uq_schema_versions_one_draft",
        table_name="schema_versions",
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.drop_index(op.f("ix_schema_versions_tenant_id"), table_name="schema_versions")
    op.drop_table("schema_versions")
    op.drop_index(
        "uq_document_types_tenant_key_live",
        table_name="document_types",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_document_types_tenant_id"), table_name="document_types")
    op.drop_table("document_types")
