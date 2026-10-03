"""page digitization

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03 21:31:36.745907+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_pages", sa.Column("text_source", sa.String(length=16), nullable=True))
    op.add_column("document_pages", sa.Column("ocr_status", sa.String(length=32), nullable=True))
    op.add_column("document_pages", sa.Column("text_quality", sa.Float(), nullable=True))
    op.add_column("document_pages", sa.Column("ocr_confidence", sa.Float(), nullable=True))
    op.add_column("document_pages", sa.Column("language", sa.String(length=8), nullable=True))
    op.add_column("document_pages", sa.Column("table_density", sa.Float(), nullable=True))
    op.add_column("document_pages", sa.Column("word_count", sa.Integer(), nullable=True))
    op.add_column("document_pages", sa.Column("layout_key", sa.String(length=512), nullable=True))
    op.add_column("document_pages", sa.Column("image_key", sa.String(length=512), nullable=True))
    op.add_column("document_pages", sa.Column("image_width", sa.Integer(), nullable=True))
    op.add_column("document_pages", sa.Column("image_height", sa.Integer(), nullable=True))
    op.add_column("document_pages", sa.Column("digitized_by_job_id", sa.UUID(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_pages", "digitized_by_job_id")
    op.drop_column("document_pages", "image_height")
    op.drop_column("document_pages", "image_width")
    op.drop_column("document_pages", "image_key")
    op.drop_column("document_pages", "layout_key")
    op.drop_column("document_pages", "word_count")
    op.drop_column("document_pages", "table_density")
    op.drop_column("document_pages", "language")
    op.drop_column("document_pages", "ocr_confidence")
    op.drop_column("document_pages", "text_quality")
    op.drop_column("document_pages", "ocr_status")
    op.drop_column("document_pages", "text_source")
