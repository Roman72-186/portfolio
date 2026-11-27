"""Freeze the control review mode at the first submission."""
import sqlalchemy as sa
from alembic import op

revision = "2db44f104ac7"
down_revision = "5e22a6d2b991"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_block_submissions", sa.Column("control_review_mode", sa.String(8), nullable=True))


def downgrade():
    op.drop_column("task_block_submissions", "control_review_mode")
