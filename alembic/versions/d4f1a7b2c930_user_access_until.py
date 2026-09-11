"""Срок доступа ученика: users.access_until — пробный набор предобучения
18-27.09 (владелец 11.09.2026).

NULL — доступ бессрочный, так живут все действующие ученики. Дата в прошлом —
ученик заходит, но видит только «Личную информацию».

Revision ID: d4f1a7b2c930
Revises: c6623ea9c67f
Create Date: 2026-09-11
"""
import sqlalchemy as sa
from alembic import op


revision = "d4f1a7b2c930"
down_revision = "c6623ea9c67f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("access_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_access_until", "users", ["access_until"])


def downgrade() -> None:
    op.drop_index("ix_users_access_until", table_name="users")
    op.drop_column("users", "access_until")
