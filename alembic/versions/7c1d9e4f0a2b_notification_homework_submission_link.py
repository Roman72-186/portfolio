"""homework_submission_id в notifications — deep-линк уведомления об
обратной связи по домашке на отдельное окно чата (владелец 10.09.2026).

Revision ID: 7c1d9e4f0a2b
Revises: 423b58d9a72a
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa


revision = "7c1d9e4f0a2b"
down_revision = "423b58d9a72a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("homework_submission_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_notifications_homework_submission_id",
        "notifications",
        "homework_submissions",
        ["homework_submission_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_notifications_homework_submission_id", "notifications", type_="foreignkey"
    )
    op.drop_column("notifications", "homework_submission_id")
