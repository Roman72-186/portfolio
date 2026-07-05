"""Drop unused granular permission tables (role_permissions, permissions)

RBAC is rank-based only (Role.rank); the only permission ever read from code
(feedback.write) was migrated to a rank check in app/api/feedback.py.

Revision ID: 375d357fbd05
Revises: b2f3e4d5c6a7
Create Date: 2026-07-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '375d357fbd05'
down_revision: Union[str, None] = 'b2f3e4d5c6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('role_permissions')
    op.drop_table('permissions')


def downgrade() -> None:
    op.create_table(
        'permissions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('codename', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('codename'),
    )
    op.create_table(
        'role_permissions',
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.Column('permission_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['permission_id'], ['permissions.id']),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id']),
        sa.PrimaryKeyConstraint('role_id', 'permission_id'),
    )
