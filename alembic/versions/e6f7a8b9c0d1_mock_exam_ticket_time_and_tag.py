"""mock exam ticket time window and tag target

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa


revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "exam_tickets",
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "exam_tickets",
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "exam_tickets",
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "exam_tickets",
        sa.Column("target_tag_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_exam_tickets_target_tag_id_tags",
        "exam_tickets",
        "tags",
        ["target_tag_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_exam_tickets_target_tag_id", "exam_tickets", ["target_tag_id"])
    op.create_index("ix_exam_tickets_time_window", "exam_tickets", ["opens_at", "closes_at"])


def downgrade() -> None:
    op.drop_index("ix_exam_tickets_time_window", table_name="exam_tickets")
    op.drop_index("ix_exam_tickets_target_tag_id", table_name="exam_tickets")
    op.drop_constraint("fk_exam_tickets_target_tag_id_tags", "exam_tickets", type_="foreignkey")
    op.drop_column("exam_tickets", "target_tag_id")
    op.drop_column("exam_tickets", "duration_minutes")
    op.drop_column("exam_tickets", "closes_at")
    op.drop_column("exam_tickets", "opens_at")
