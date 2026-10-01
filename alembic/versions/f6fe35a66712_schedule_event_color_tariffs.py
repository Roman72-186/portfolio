"""schedule_event_color_tariffs

Календарь дайджеста у ученика (созвон 30.09.2026, владелец 01.10.2026:
«сетка + цвета + тарифы у события»): у события появляются цвет метки и
тарифы, которым оно показывается.

`schedule_events.color` — ключ палитры, у существующих событий станет
`purple` (цвет бренда) через server_default, бэкфилл не нужен.
`schedule_event_tariffs` — пусто значит «всем тарифам», так старые события
остаются видны всем, как и были.

Revision ID: f6fe35a66712
Revises: 9f1ade97eedb
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6fe35a66712'
down_revision: Union[str, None] = '9f1ade97eedb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'schedule_events',
        sa.Column('color', sa.String(16), nullable=False, server_default='purple'),
    )
    op.create_table(
        'schedule_event_tariffs',
        sa.Column(
            'event_id', sa.Integer(),
            sa.ForeignKey('schedule_events.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('tariff', sa.String(50), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table('schedule_event_tariffs')
    op.drop_column('schedule_events', 'color')
