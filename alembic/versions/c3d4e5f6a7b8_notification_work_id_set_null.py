"""notification work_id on delete set null

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('notifications_work_id_fkey', 'notifications', type_='foreignkey')
    op.create_foreign_key(
        'notifications_work_id_fkey', 'notifications', 'works',
        ['work_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('notifications_work_id_fkey', 'notifications', type_='foreignkey')
    op.create_foreign_key(
        'notifications_work_id_fkey', 'notifications', 'works',
        ['work_id'], ['id'],
    )
