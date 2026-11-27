"""Remember create requests and retry deletion of work files after commit."""
from alembic import op
import sqlalchemy as sa

revision = "f84d92b13c50"
down_revision = "ad71e4b09c52"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tracker_tasks", sa.Column("creation_request_id", sa.String(36), nullable=True))
    op.create_index("ix_tracker_tasks_creation_request_id", "tracker_tasks", ["creation_request_id"], unique=True)
    op.create_table(
        "pending_work_file_deletions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_table("pending_work_file_deletions")
    op.drop_index("ix_tracker_tasks_creation_request_id", table_name="tracker_tasks")
    op.drop_column("tracker_tasks", "creation_request_id")
