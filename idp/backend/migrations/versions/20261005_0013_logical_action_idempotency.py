"""logical action idempotency (F14)

The action idempotency key names the logical action (document, page range,
action) instead of the job, so replays reuse it; at most one run per key may
succeed by executing, later runs reference it.

Runs created before this revision keep their per-job keys.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-05 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("action_runs", sa.Column("deduplicated_from_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_action_runs_deduplicated_from_id_action_runs"),
        "action_runs",
        "action_runs",
        ["deduplicated_from_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_index("uq_action_runs_idempotency_key", table_name="action_runs")
    op.create_index(
        "uq_action_runs_job_key", "action_runs", ["job_id", "idempotency_key"], unique=True
    )
    op.create_index(
        "uq_action_runs_executed_key",
        "action_runs",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded' AND deduplicated_from_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_action_runs_executed_key",
        table_name="action_runs",
        postgresql_where=sa.text("status = 'succeeded' AND deduplicated_from_id IS NULL"),
    )
    op.drop_index("uq_action_runs_job_key", table_name="action_runs")
    # Logical keys repeat across jobs; restore the per-job form before the global index.
    op.execute(
        "UPDATE action_runs SET idempotency_key = "
        "job_id::text || ':' || part_id::text || ':' || name"
    )
    op.create_index(
        "uq_action_runs_idempotency_key", "action_runs", ["idempotency_key"], unique=True
    )
    op.drop_constraint(
        op.f("fk_action_runs_deduplicated_from_id_action_runs"), "action_runs", type_="foreignkey"
    )
    op.drop_column("action_runs", "deduplicated_from_id")
