"""add birthday reminder tracking to users

Revision ID: c141cbbb7606
Revises: ae81b7aec193
Create Date: 2026-09-12 22:19:17.385828

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c141cbbb7606'
down_revision: Union[str, None] = 'ae81b7aec193'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('birthday_reminder_sent_year', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'birthday_reminder_sent_year')
