"""School timing and attendance additions, 09.11.2026.

Existing submissions retain their original measurement; no backfill.
Lesson reminders remain off until staff sets an explicit lesson ending.
"""
from alembic import op
import sqlalchemy as sa

revision = "b72e8a4c190f"
down_revision = "a61f0e9c73d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_block_states", sa.Column(
        "work_finished_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("lesson_attendance_marks", sa.Column("attendance_format", sa.String(20), nullable=True))
    op.add_column("task_blocks", sa.Column("lesson_ends_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("task_blocks", "lesson_ends_at")
    op.drop_column("lesson_attendance_marks", "attendance_format")
    op.drop_column("task_block_states", "work_finished_at")
