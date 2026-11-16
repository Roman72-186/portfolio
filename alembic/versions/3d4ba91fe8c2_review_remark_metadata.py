"""Store a review remark's section and target submission."""

import sqlalchemy as sa
from alembic import op

revision = "3d4ba91fe8c2"
down_revision = "9f3eb14dc683"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_block_feedback_messages", sa.Column("review_section", sa.String(24)))
    op.add_column("task_block_feedback_messages", sa.Column("review_submission_id", sa.Integer()))


def downgrade():
    op.drop_column("task_block_feedback_messages", "review_submission_id")
    op.drop_column("task_block_feedback_messages", "review_section")
