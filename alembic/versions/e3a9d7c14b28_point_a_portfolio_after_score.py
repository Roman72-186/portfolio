"""точка А: оценка Главного преподавателя за набор работ «После»

Лиза 14.09.2026 (голосовое про экран оценки точки А): «портфолио до, то есть
это то, что он отправил до начала предобучения… портфолио после предобучения
вижу, что не оценено». Вторая плашка того же экрана, парная к
`portfolio_before_score` из миграции 9e2d3c4b5a6f.

Три колонки на ученике, без новой таблицы: оценка ровно одна на ученика за
весь набор, как и у «До». Шкала 0–100, как у `works.score`. Существующие
данные не трогаются: у всех балл пуст, плашка на экране точки А показывается
неоценённой — это ожидаемое поведение, а не сбой.

Revision ID: e3a9d7c14b28
Revises: c141cbbb7606
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3a9d7c14b28'
down_revision: Union[str, None] = 'c141cbbb7606'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("portfolio_after_score", sa.Integer(), nullable=True))
    op.add_column(
        "users",
        sa.Column("portfolio_after_scored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("users", sa.Column("portfolio_after_scored_by_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_users_portfolio_after_scored_by_id_users",
        "users",
        "users",
        ["portfolio_after_scored_by_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_users_portfolio_after_scored_by_id_users", "users", type_="foreignkey"
    )
    op.drop_column("users", "portfolio_after_scored_by_id")
    op.drop_column("users", "portfolio_after_scored_at")
    op.drop_column("users", "portfolio_after_score")
