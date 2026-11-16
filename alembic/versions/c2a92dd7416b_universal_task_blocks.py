"""Store universal card membership on existing task blocks."""

import sqlalchemy as sa
from alembic import op

revision = "c2a92dd7416b"
down_revision = "b16c03e1a942"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("works", sa.Column("portfolio_source_block_id", sa.Integer()))
    op.add_column("task_blocks", sa.Column("universal_key", sa.String(32)))
    op.add_column("task_blocks", sa.Column("portfolio_month", sa.String(7)))
    op.create_table(
        "task_block_upload_receipts",
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey(
            "task_block_submissions.id", ondelete="CASCADE"
        ), primary_key=True),
        sa.Column("request_key", sa.String(32), primary_key=True),
        sa.Column("created_count", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_column("works", "portfolio_source_block_id")
    op.drop_table("task_block_upload_receipts")
    op.drop_column("task_blocks", "portfolio_month")
    op.drop_column("task_blocks", "universal_key")

