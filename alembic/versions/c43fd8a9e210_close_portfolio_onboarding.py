"""Remember when a student closes the Before upload window."""
import sqlalchemy as sa
from alembic import op

revision = "c43fd8a9e210"
down_revision = "6a2d9c47b810"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("portfolio_onboardings", sa.Column("closed_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("portfolio_onboardings", "closed_at")
