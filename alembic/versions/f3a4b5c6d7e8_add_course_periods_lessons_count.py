"""add course_periods and lessons_count to users

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa


revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("course_periods", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("lessons_count", sa.String(5), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "lessons_count")
    op.drop_column("users", "course_periods")
