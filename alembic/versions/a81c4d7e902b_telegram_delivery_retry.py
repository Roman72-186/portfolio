"""Durable Telegram notification delivery, 11.11.2026."""
from alembic import op
import sqlalchemy as sa

revision = "a81c4d7e902b"
down_revision = "e731c08a4bd2"
branch_labels = None
depends_on = None


def upgrade():
    # No historical replay: only explicitly queued rows are retried.
    op.add_column("notifications", sa.Column("telegram_status", sa.String(16), nullable=True))
    op.add_column("notifications", sa.Column("telegram_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("notifications", sa.Column("telegram_next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notifications", sa.Column("telegram_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_notifications_telegram_due", "notifications", ["telegram_status", "telegram_next_attempt_at"])


def downgrade():
    op.drop_index("ix_notifications_telegram_due", table_name="notifications")
    for column in ("telegram_sent_at", "telegram_next_attempt_at", "telegram_attempts", "telegram_status"):
        op.drop_column("notifications", column)
