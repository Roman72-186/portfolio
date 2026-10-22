"""broadcast_excluded_students — исключение из рассылки поимённо (22.10.2026)

Владелец 22.10.2026: «в рассылку добавить функцию, чтобы исключать из неё по
юзернейму». Выбрали тариф — исключённые не получат, даже если подходят по
тарифу и уровню или выбраны поимённо.

Только новая таблица, данные не трогаются. Откат: таблица удаляется, у
черновиков пропадает список исключённых — при следующей отправке они снова
попадут под выбор.

Revision ID: f2b6d8a1c3e5
Revises: 535198ed8a18
Create Date: 2026-10-22 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b6d8a1c3e5'
down_revision: Union[str, None] = '535198ed8a18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broadcast_excluded_students",
        sa.Column(
            "broadcast_id", sa.Integer(),
            sa.ForeignKey("broadcasts.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("broadcast_excluded_students")
