"""add student tag fields to users

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-05-20
"""
from alembic import op
import sqlalchemy as sa


revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("exam_dates", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("exam_subjects", sa.String(20), nullable=True))
    op.add_column("users", sa.Column("study_mode", sa.String(10), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_publishable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("users", "is_publishable", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "is_publishable")
    op.drop_column("users", "study_mode")
    op.drop_column("users", "exam_subjects")
    op.drop_column("users", "exam_dates")
