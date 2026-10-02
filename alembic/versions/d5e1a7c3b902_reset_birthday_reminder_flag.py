"""reset_birthday_reminder_flag

Напоминание о дне рождения — только в сам день (владелец 02.10.2026), а не
за 7 дней. У учеников с днём рождения в ближайшую неделю флаг
`users.birthday_reminder_sent_year` уже стоит от старого напоминания «за
неделю» — без сброса новое «сегодня» им бы не пришло.

Сброс у всех безопасен: при новой логике флаг смотрится только в сам день
рождения, а проверка идёт по cron в 05:00 UTC — выкатка повторного
сообщения в тот же день не вызовет, если не совпадёт с этим часом.
Даунгрейд флаг не восстанавливает: старых значений нет.

Revision ID: d5e1a7c3b902
Revises: 84262255ff23
Create Date: 2026-10-02 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd5e1a7c3b902'
down_revision: Union[str, None] = '84262255ff23'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE users SET birthday_reminder_sent_year = NULL")


def downgrade() -> None:
    pass
