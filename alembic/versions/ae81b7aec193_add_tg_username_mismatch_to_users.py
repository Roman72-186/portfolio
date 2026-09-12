"""add tg_username_mismatch to users

Revision ID: ae81b7aec193
Revises: 302789ea8a69
Create Date: 2026-09-12 21:45:57.186034

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ae81b7aec193'
down_revision: Union[str, None] = '302789ea8a69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('tg_username_mismatch', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('users', 'tg_username_mismatch')
