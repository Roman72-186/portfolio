"""Separate drawing and upload time, 09.11.2026.

Existing submissions retain their original measurement; no backfill.
"""
from alembic import op
import sqlalchemy as sa

revision = "b72e8a4c190f"
down_revision = "a61f0e9c73d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_block_states", sa.Column(
        "work_finished_at", sa.DateTime(timezone=True), nullable=True,
    ))


def downgrade():
    op.drop_column("task_block_states", "work_finished_at")
