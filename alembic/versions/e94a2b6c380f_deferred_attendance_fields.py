"""Deferred attendance/reminder fields, separate from points 3 and 6.

Not part of the 09.11.2026 timer/statistics release.
"""
from alembic import op
import sqlalchemy as sa

revision = "e94a2b6c380f"
down_revision = "b72e8a4c190f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("lesson_attendance_marks", sa.Column("attendance_format", sa.String(20), nullable=True))
    op.add_column("task_blocks", sa.Column("lesson_ends_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("task_blocks", "lesson_ends_at")
    op.drop_column("lesson_attendance_marks", "attendance_format")
