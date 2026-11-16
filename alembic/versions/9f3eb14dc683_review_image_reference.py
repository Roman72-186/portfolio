"""Keep the submitted image referenced by a review remark."""

import sqlalchemy as sa
from alembic import op

revision = "9f3eb14dc683"
down_revision = "c2a92dd7416b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_block_feedback_messages", sa.Column("review_image_id", sa.Integer()))


def downgrade():
    op.drop_column("task_block_feedback_messages", "review_image_id")
