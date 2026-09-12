"""add profile fields birth date city timezone vk parent name sdek email

Revision ID: 302789ea8a69
Revises: 88aa51ea931b
Create Date: 2026-09-12 21:27:22.626305

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '302789ea8a69'
down_revision: Union[str, None] = '88aa51ea931b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('birth_date', sa.Date(), nullable=True))
    op.add_column('users', sa.Column('city', sa.String(length=100), nullable=True))
    op.add_column('users', sa.Column('timezone', sa.String(length=20), nullable=True))
    # vk_profile_url, sdek_address, email — контактные поля, шифруются как phone/tg_username
    # (EncryptedString хранит TEXT независимо от length, см. app/crypto.py).
    op.add_column('users', sa.Column('vk_profile_url', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('parent_name', sa.String(length=150), nullable=True))
    op.add_column('users', sa.Column('sdek_address', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('email', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'email')
    op.drop_column('users', 'sdek_address')
    op.drop_column('users', 'parent_name')
    op.drop_column('users', 'vk_profile_url')
    op.drop_column('users', 'timezone')
    op.drop_column('users', 'city')
    op.drop_column('users', 'birth_date')
