"""payments paid_until_before — срок до оплаты, для отмены отметки и возврата (07.10.2026)

План — `plans/2026-09-30-apparchi-monthly-payments.md`, раздел «Ручная отметка
оплаты». Ручную отметку ГП может отменить, возврат закрывает оплаченный
месяц — в обоих случаях «оплачено по» ученика надо вернуть назад. Из одних
оставшихся платежей его не восстановить: срок мог стоять из карточки или
загрузки списка, без строки платежа. Поэтому платёж помнит, каким был срок
до него. Пусто — платёж срок не двигал (или до него срока не было).

Откат — снос колонки; отмена старых отметок тогда вернёт срок только по
оставшимся платежам.

Revision ID: 904398797db7
Revises: 43e99f6a08dd
Create Date: 2026-10-07 23:57:24.115814

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '904398797db7'
down_revision: Union[str, None] = '43e99f6a08dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("paid_until_before", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "paid_until_before")
