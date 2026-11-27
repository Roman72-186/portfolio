"""First-week attendance format only; no absence-reminder fields, 10.11.2026."""
from alembic import op
import sqlalchemy as sa

revision = "a103c7d9e5f2"
down_revision = "d7a6e5b4c321"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("lesson_attendance_marks", sa.Column("attendance_format", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("lesson_attendance_marks", "attendance_format")
