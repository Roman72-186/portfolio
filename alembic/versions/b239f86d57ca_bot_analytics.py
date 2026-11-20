"""Marketing links and Telegram funnel events."""
import sqlalchemy as sa
from alembic import op

revision = "b239f86d57ca"
down_revision = "3d4ba91fe8c2"
branch_labels = depends_on = None


def upgrade():
    op.create_table("bot_links",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(150), nullable=False),
        *(sa.Column(name, sa.String(150), nullable=False) for name in
          ("utm_source", "utm_medium", "utm_campaign", "utm_content")),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table("bot_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(32), unique=True),
        sa.Column("payment_ref", sa.String(64), index=True),
        sa.Column("uid", sa.BigInteger()),
        sa.Column("code", sa.String(32), sa.ForeignKey("bot_links.code")),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("value", sa.String(150), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_bot_events_at", "bot_events", ["at"])
    op.create_index("ix_bot_events_user", "bot_events", ["uid", "kind", "id"])


def downgrade():
    op.drop_table("bot_events")
    op.drop_table("bot_links")
