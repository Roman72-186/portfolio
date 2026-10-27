"""Посещение занятия: запись занятия необязательна тем, кто был.

Александрия 27.10.2026: нажал «Подключиться к занятию» — был на занятии, и
запись занятия ему необязательна; не нажал — обязательна. Владелец: ручную
отметку «был / не был» ставят ГП и суперадмин.

- `task_blocks.optional_if_attended` — флаг у видео, по умолчанию выключен:
  у уже созданных блоков поведение не меняется;
- `lesson_attendance_marks` — ручная отметка по кнопке «Ссылка», перекрывает
  нажатие в обе стороны.

Revision ID: b8d2f4a61c90
Revises: e3f9a1c7b2d4
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa


revision = "b8d2f4a61c90"
down_revision = "e3f9a1c7b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_blocks",
        sa.Column(
            "optional_if_attended",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "lesson_attendance_marks",
        sa.Column("block_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("attended", sa.Boolean(), nullable=False),
        sa.Column("marked_by_id", sa.Integer(), nullable=True),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["block_id"], ["task_blocks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["marked_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("block_id", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("lesson_attendance_marks")
    op.drop_column("task_blocks", "optional_if_attended")
