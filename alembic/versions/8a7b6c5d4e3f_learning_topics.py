"""learning topics: own weekly topics for video lessons, decoupled from exams

Тема недели становится собственной сущностью видеомодуля. Прежняя привязка урока
к ExamAssignment (ревизия 6e5d4c3b2a19) убирается: она переплетала видеоуроки с
пробниками — урок мог выпасть ученику как вариант пробника, а создание темы
открывало период сдачи пробника.

Данных не теряем: на момент миграции learning_videos.assignment_id заполнен у нуля
записей (проверено на проде 06.08.2026).

Revision ID: 8a7b6c5d4e3f
Revises: 6e5d4c3b2a19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a7b6c5d4e3f"
down_revision: Union[str, None] = "6e5d4c3b2a19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learning_topics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("assign_to_all", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["published_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_learning_topics_public", "learning_topics", ["is_published", "opens_at"]
    )

    op.create_table(
        "learning_topic_tags",
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("topic_id", "tag_id"),
    )
    op.create_index("ix_learning_topic_tags_tag", "learning_topic_tags", ["tag_id"])

    op.create_table(
        "learning_topic_assignees",
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["learning_topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("topic_id", "user_id"),
    )
    op.create_index(
        "ix_learning_topic_assignees_user", "learning_topic_assignees", ["user_id"]
    )

    op.add_column("learning_videos", sa.Column("topic_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_learning_videos_topic",
        "learning_videos",
        "learning_topics",
        ["topic_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_learning_videos_topic", "learning_videos", ["topic_id"])

    op.drop_index("ix_learning_videos_assignment", table_name="learning_videos")
    op.drop_constraint("fk_learning_videos_assignment", "learning_videos", type_="foreignkey")
    op.drop_column("learning_videos", "assignment_id")


def downgrade() -> None:
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

    op.drop_index("ix_learning_videos_topic", table_name="learning_videos")
    op.drop_constraint("fk_learning_videos_topic", "learning_videos", type_="foreignkey")
    op.drop_column("learning_videos", "topic_id")

    op.drop_index("ix_learning_topic_assignees_user", table_name="learning_topic_assignees")
    op.drop_table("learning_topic_assignees")
    op.drop_index("ix_learning_topic_tags_tag", table_name="learning_topic_tags")
    op.drop_table("learning_topic_tags")
    op.drop_index("ix_learning_topics_public", table_name="learning_topics")
    op.drop_table("learning_topics")
