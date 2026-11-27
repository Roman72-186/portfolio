"""Store the ancestor path of assignment blocks."""

import sqlalchemy as sa
from alembic import op

revision = "ad71e4b09c52"
down_revision = "b7d1a65e2f90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_blocks", sa.Column("container_path", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("task_blocks", "container_path")
