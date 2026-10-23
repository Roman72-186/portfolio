"""«Был на платформе» и время на платформе (23.10.2026)

Александрия 22.10.2026: «был 5 дней назад», хотя ребёнок заходит каждый
день. `users.last_login_at` пишется только при новой сессии, а сессия
продлевается сама — дата застывала. Новое поле `users.last_seen_at` ставит
пинг открытой вкладки ученика, таблица `user_activity_days` копит время
на платформе по дням (`services/presence.py`).

Прошлое время не восстановить. «Был на платформе» до первого пинга —
последний вход: поле заполняется из `last_login_at`.

Revision ID: 4a83c858efbb
Revises: c4d1e7a9f3b2
Create Date: 2026-10-23 22:51:50.279335

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a83c858efbb'
down_revision: Union[str, None] = 'c4d1e7a9f3b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE users SET last_seen_at = last_login_at "
        "WHERE last_seen_at IS NULL AND last_login_at IS NOT NULL"
    )
    op.create_table(
        "user_activity_days",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "day", name="uq_user_activity_days_user_day"),
    )
    op.create_index("ix_user_activity_days_day", "user_activity_days", ["day"])


def downgrade() -> None:
    op.drop_index("ix_user_activity_days_day", table_name="user_activity_days")
    op.drop_table("user_activity_days")
    op.drop_column("users", "last_seen_at")
