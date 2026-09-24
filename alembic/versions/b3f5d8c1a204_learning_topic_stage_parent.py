"""learning topic stage parent (Этапы)

Владелец 24.09.2026: иерархия Этап → Цикл → Задание. Этап — крупный период
(«месяц», «Предобучение»), группирующий несколько циклов подряд. Отдельной
таблицы под этап не заводим — вторая сущность расписания в проекте запрещена
(`app/api/cabinet_program.py`), поэтому этап — такая же `LearningTopic`, с
новым значением `kind='stage'`.

`learning_topics.parent_id` — необязательная self-ссылка: у цикла (`kind=
'week'`) указывает на его этап, у самого этапа всегда `NULL`. `ON DELETE SET
NULL`, а не `RESTRICT` — удаление `LearningTopic` в проекте всегда мягкое
(`deleted_at`), FK почти никогда не сработает через штатный код, а `SET NULL`
не уронит целостность циклов-детей, если когда-нибудь понадобится физическая
очистка.

Backfill не нужен: все существующие циклы получают `parent_id = NULL` —
легаси-архив без привязки к этапу, осознанное поведение, не брошенные данные.

Revision ID: b3f5d8c1a204
Revises: a7d363b096bd
Create Date: 2026-09-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f5d8c1a204'
down_revision: Union[str, None] = 'a7d363b096bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "learning_topics",
        sa.Column("parent_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_learning_topics_parent_id",
        "learning_topics",
        "learning_topics",
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_learning_topics_parent", "learning_topics", ["parent_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_learning_topics_parent", table_name="learning_topics")
    op.drop_constraint(
        "fk_learning_topics_parent_id", "learning_topics", type_="foreignkey"
    )
    op.drop_column("learning_topics", "parent_id")
