"""точка А: оценка Главного преподавателя за набор работ «До»

Владелец 09.09.2026: «по этой кнопке работы загружаются в ДО и их может
оценить ГП, это будет для высчета среднего значения в точке А», «оценку
должен ГП ставит общую по всем работам, не для каждой».

Три колонки на ученике, без новой таблицы: оценка ровно одна на ученика, как
и соседний `portfolio_do_completed`. Шкала 0–100, как у `works.score`.
Существующие данные не трогаются: у всех учеников балл пуст, и карточка
«точка А» на экране проверки у Главного преподавателя появится как
непроверенная — это ожидаемое поведение, а не сбой.

Revision ID: 9e2d3c4b5a6f
Revises: 8d1c2b3a4e5f
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e2d3c4b5a6f'
down_revision: Union[str, None] = '8d1c2b3a4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("portfolio_before_score", sa.Integer(), nullable=True))
    op.add_column(
        "users",
        sa.Column("portfolio_before_scored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("users", sa.Column("portfolio_before_scored_by_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_users_portfolio_before_scored_by_id_users",
        "users",
        "users",
        ["portfolio_before_scored_by_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_users_portfolio_before_scored_by_id_users", "users", type_="foreignkey"
    )
    op.drop_column("users", "portfolio_before_scored_by_id")
    op.drop_column("users", "portfolio_before_scored_at")
    op.drop_column("users", "portfolio_before_score")
