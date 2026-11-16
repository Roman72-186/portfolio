"""Record when a student's current curator was assigned."""

import sqlalchemy as sa
from alembic import op

revision = "b16c03e1a942"
down_revision = "e94a2b6c380f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("curator_assigned_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("users", "curator_assigned_at")
