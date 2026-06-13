"""add tags and user_tags

Revision ID: b3c4d5e6f7a8
Revises: a4b5c6d7e8f9
Create Date: 2026-06-11
"""
from alembic import op
import sqlalchemy as sa


revision = "b3c4d5e6f7a8"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", name="uq_tags_name"),
    )
    op.create_table(
        "user_tags",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_tags_user", "user_tags", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_tags_user", table_name="user_tags")
    op.drop_table("user_tags")
    op.drop_table("tags")
