"""learning_videos: три поля вопросов мини-опроса + таблица ответов ученика

Revision ID: a1c7e4f2b809
Revises: 5e71d0328939
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa


revision = "a1c7e4f2b809"
down_revision = "5e71d0328939"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "learning_videos",
        sa.Column("quiz_question_1", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "learning_videos",
        sa.Column("quiz_question_2", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "learning_videos",
        sa.Column("quiz_question_3", sa.String(length=300), nullable=True),
    )
    op.create_table(
        "video_quiz_responses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "video_id",
            sa.Integer(),
            sa.ForeignKey("learning_videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("answer_1", sa.Text(), nullable=True),
        sa.Column("answer_2", sa.Text(), nullable=True),
        sa.Column("answer_3", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("video_id", "user_id", name="uq_video_quiz_response_video_user"),
    )
    op.create_index(
        "ix_video_quiz_responses_video", "video_quiz_responses", ["video_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_video_quiz_responses_video", table_name="video_quiz_responses")
    op.drop_table("video_quiz_responses")
    op.drop_column("learning_videos", "quiz_question_3")
    op.drop_column("learning_videos", "quiz_question_2")
    op.drop_column("learning_videos", "quiz_question_1")
