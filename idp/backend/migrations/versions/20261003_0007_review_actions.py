"""review actions

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03 22:01:59.930350+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_actions",
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("field_id", sa.UUID(), nullable=True),
        sa.Column("path", sa.String(length=255), nullable=True),
        sa.Column("row_id", sa.String(length=32), nullable=True),
        sa.Column("original_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("corrected_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_review_actions_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["extracted_fields.id"],
            name=op.f("fk_review_actions_field_id_extracted_fields"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["review_tasks.id"],
            name=op.f("fk_review_actions_task_id_review_tasks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_review_actions_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_actions")),
    )
    op.create_index(
        "ix_review_actions_task", "review_actions", ["task_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_review_actions_tenant_id"), "review_actions", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_review_actions_tenant_id"), table_name="review_actions")
    op.drop_index("ix_review_actions_task", table_name="review_actions")
    op.drop_table("review_actions")
