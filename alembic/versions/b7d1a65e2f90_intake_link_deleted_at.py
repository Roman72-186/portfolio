"""Archive intake links without removing purchase history."""
import sqlalchemy as sa
from alembic import op

revision = "b7d1a65e2f90"
down_revision = "9c54b7a82f01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("intake_links", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("intake_links", "deleted_at")
