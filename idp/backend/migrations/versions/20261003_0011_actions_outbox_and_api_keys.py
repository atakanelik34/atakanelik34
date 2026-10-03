"""actions outbox and api keys

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-03 22:51:05.872794+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "deliveries",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_error", sa.String(length=200), nullable=True),
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
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_outbox_events_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
    )
    op.create_index(
        "ix_outbox_events_pending",
        "outbox_events",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_index(
        op.f("ix_outbox_events_tenant_id"), "outbox_events", ["tenant_id"], unique=False
    )
    op.create_table(
        "api_keys",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_id", sa.UUID(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_api_keys_created_by_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_api_keys_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
    )
    op.create_index(op.f("ix_api_keys_tenant_id"), "api_keys", ["tenant_id"], unique=False)
    op.create_index("uq_api_keys_prefix", "api_keys", ["prefix"], unique=True)
    op.create_table(
        "action_runs",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("part_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("connection_key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "response",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("external_reference", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("decided_by_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["decided_by_id"],
            ["users.id"],
            name=op.f("fk_action_runs_decided_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_action_runs_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["processing_jobs.id"],
            name=op.f("fk_action_runs_job_id_processing_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["part_id"],
            ["document_parts.id"],
            name=op.f("fk_action_runs_part_id_document_parts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_action_runs_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_runs")),
    )
    op.create_index(
        "ix_action_runs_document", "action_runs", ["document_id", "created_at"], unique=False
    )
    op.create_index(op.f("ix_action_runs_tenant_id"), "action_runs", ["tenant_id"], unique=False)
    op.create_index(
        "uq_action_runs_idempotency_key", "action_runs", ["idempotency_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_action_runs_idempotency_key", table_name="action_runs")
    op.drop_index(op.f("ix_action_runs_tenant_id"), table_name="action_runs")
    op.drop_index("ix_action_runs_document", table_name="action_runs")
    op.drop_table("action_runs")
    op.drop_index("uq_api_keys_prefix", table_name="api_keys")
    op.drop_index(op.f("ix_api_keys_tenant_id"), table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index(op.f("ix_outbox_events_tenant_id"), table_name="outbox_events")
    op.drop_index(
        "ix_outbox_events_pending",
        table_name="outbox_events",
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.drop_table("outbox_events")
