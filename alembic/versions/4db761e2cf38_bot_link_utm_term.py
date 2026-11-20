"""Store the optional keyword for Telegram campaign links."""
import sqlalchemy as sa
from alembic import op

revision = "4db761e2cf38"
down_revision = "913c5ab7e204"
branch_labels = depends_on = None


def upgrade():
    op.add_column("bot_links", sa.Column("utm_term", sa.String(150), nullable=False, server_default=""))


def downgrade():
    op.drop_column("bot_links", "utm_term")
