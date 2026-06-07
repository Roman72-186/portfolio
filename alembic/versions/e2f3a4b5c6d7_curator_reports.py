"""curator_reports

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-02
"""
from alembic import op
import sqlalchemy as sa


revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "curator_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("curator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("video_url", sa.String(length=500), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_curator_reports_curator_id", "curator_reports", ["curator_id"]
    )
    op.create_index(
        "ix_curator_reports_curator_created",
        "curator_reports",
        ["curator_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_curator_reports_curator_created", table_name="curator_reports")
    op.drop_index("ix_curator_reports_curator_id", table_name="curator_reports")
    op.drop_table("curator_reports")
