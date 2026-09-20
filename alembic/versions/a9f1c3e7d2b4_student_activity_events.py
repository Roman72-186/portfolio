"""add append-only student activity journal

Revision ID: a9f1c3e7d2b4
Revises: f8e2c4a6d1b3
Create Date: 2026-09-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9f1c3e7d2b4"
down_revision: Union[str, None] = "f8e2c4a6d1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "student_activity_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("details", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_student_activity_events_user_created",
        "student_activity_events",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_student_activity_events_type_created",
        "student_activity_events",
        ["event_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_student_activity_events_type_created", table_name="student_activity_events")
    op.drop_index("ix_student_activity_events_user_created", table_name="student_activity_events")
    op.drop_table("student_activity_events")
