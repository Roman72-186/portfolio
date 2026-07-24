"""legacy_portfolio_photos

Revision ID: 13ee8d6041e3
Revises: f9a0b1c2d3e4
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa


revision = "13ee8d6041e3"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "legacy_portfolio_photos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("dialog_id", sa.String(length=30), nullable=False),
        sa.Column("month", sa.String(length=20), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("s3_path", sa.String(length=400), nullable=False),
        sa.Column("s3_url", sa.String(length=500), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("s3_path", name="uq_legacy_portfolio_s3_path"),
    )
    op.create_index(
        "ix_legacy_portfolio_photos_user_id", "legacy_portfolio_photos", ["user_id"]
    )
    op.create_index(
        "ix_legacy_portfolio_photos_dialog_id", "legacy_portfolio_photos", ["dialog_id"]
    )
    op.create_index(
        "ix_legacy_portfolio_user_year_month",
        "legacy_portfolio_photos",
        ["user_id", "year", "month"],
    )


def downgrade() -> None:
    op.drop_index("ix_legacy_portfolio_user_year_month", table_name="legacy_portfolio_photos")
    op.drop_index("ix_legacy_portfolio_photos_dialog_id", table_name="legacy_portfolio_photos")
    op.drop_index("ix_legacy_portfolio_photos_user_id", table_name="legacy_portfolio_photos")
    op.drop_table("legacy_portfolio_photos")
