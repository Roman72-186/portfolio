"""exam_cycles.revision_requested_at

Revision ID: c4d5e6f7a8b9
Revises: 2a2364e85725
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "2a2364e85725"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "exam_cycles",
        sa.Column("revision_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("exam_cycles", "revision_requested_at")
