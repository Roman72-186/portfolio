"""schedule event time

Время занятия у события дайджеста (владелец 05.10.2026: «да, по желанию»).
До этого время вписывали в название: на проде 04.10.2026 Александрия завела
«тренировка РИСУНОК 10:00-11:30», «Р+К очно 16:00-19:00». Два необязательных
поля «с» и «до»; у события без времени оба пустые.

Перенос времени из названий уже заведённых событий — не здесь, а скриптом
`scripts/digest_event_times.py`: названия пишут люди, и что именно он
поменяет, сначала показывается без записи.

Revision ID: a206e833f766
Revises: 639c04979ebf
Create Date: 2026-10-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a206e833f766"
down_revision: Union[str, None] = "639c04979ebf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("schedule_events", sa.Column("time_from", sa.Time(), nullable=True))
    op.add_column("schedule_events", sa.Column("time_to", sa.Time(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("schedule_events") as batch:
        batch.drop_column("time_to")
        batch.drop_column("time_from")
