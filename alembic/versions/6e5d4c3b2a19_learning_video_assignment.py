"""learning video belongs to an assignment (weekly topic)

Revision ID: 6e5d4c3b2a19
Revises: 7f4e3d2c1b0a
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6e5d4c3b2a19"
down_revision: Union[str, None] = "7f4e3d2c1b0a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL = урок открыт всем ученикам (поведение до появления тем). Существующие
    # ролики остаются глобальными, привязка проставляется вручную из админки.
    op.add_column("learning_videos", sa.Column("assignment_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_learning_videos_assignment",
        "learning_videos",
        "exam_assignments",
        ["assignment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_learning_videos_assignment", "learning_videos", ["assignment_id"])


def downgrade() -> None:
    op.drop_index("ix_learning_videos_assignment", table_name="learning_videos")
    op.drop_constraint("fk_learning_videos_assignment", "learning_videos", type_="foreignkey")
    op.drop_column("learning_videos", "assignment_id")
