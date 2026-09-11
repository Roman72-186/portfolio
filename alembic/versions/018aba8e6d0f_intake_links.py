"""Ссылка пробного набора предобучения: таблица intake_links.

Одна строка на slug (`apparchi.ru/<slug>`) — дата и выключатель живут в базе,
не константой в коде (см. app/models/intake_link.py).

Revision ID: 018aba8e6d0f
Revises: d4f1a7b2c930
Create Date: 2026-09-11
"""
import sqlalchemy as sa
from alembic import op


revision = "018aba8e6d0f"
down_revision = "d4f1a7b2c930"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intake_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("access_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_intake_links_slug", "intake_links", ["slug"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_intake_links_slug", table_name="intake_links")
    op.drop_table("intake_links")
