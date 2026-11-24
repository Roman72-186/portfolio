"""Store sale and access windows for new intake flows."""
import sqlalchemy as sa
from alembic import op

revision = "c84f6d8e1702"
down_revision = "29f5f31e81b7"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("sales_from", "sales_until", "access_from"):
        op.add_column("intake_links", sa.Column(name, sa.DateTime(timezone=True)))


def downgrade():
    for name in ("access_from", "sales_until", "sales_from"):
        op.drop_column("intake_links", name)
