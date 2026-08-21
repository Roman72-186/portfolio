"""homework submission + feedback — сдача домашки и диалог обратной связи

Симметрично homework_assignments/homework_images (сторона задания) и
feedbacks/feedback_messages (диалог пробника), но без билета/цикла/попытки —
у домашки их нет и не будет (решение владельца Р2, TODO.md §0).

Revision ID: d499b1aad9ba
Revises: 1f6315c0983e
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa


revision = "d499b1aad9ba"
down_revision = "1f6315c0983e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "homework_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "homework_id",
            sa.Integer(),
            sa.ForeignKey("homework_assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tracker_task_id",
            sa.Integer(),
            sa.ForeignKey("tracker_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="submitted"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_homework_submissions_task_user",
        "homework_submissions",
        ["tracker_task_id", "user_id"],
        unique=True,
    )
    op.create_index(
        "ix_homework_submissions_user", "homework_submissions", ["user_id"]
    )

    op.create_table(
        "homework_submission_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("homework_submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("image_s3_url", sa.String(length=500), nullable=False),
        sa.Column("image_s3_path", sa.String(length=300), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_homework_submission_images_submission",
        "homework_submission_images",
        ["submission_id", "sort_order"],
    )

    op.create_table(
        "homework_feedbacks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "submission_id",
            sa.Integer(),
            sa.ForeignKey("homework_submissions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("curator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "homework_feedback_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "feedback_id",
            sa.Integer(),
            sa.ForeignKey("homework_feedbacks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sender_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sender_role", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("photo_s3_path", sa.String(length=500), nullable=True),
        sa.Column("photo_s3_url", sa.String(length=500), nullable=True),
        sa.Column("video_s3_path", sa.String(length=500), nullable=True),
        sa.Column("video_s3_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(text IS NOT NULL AND length(text) > 0) "
            "OR (photo_s3_url IS NOT NULL) OR (video_s3_url IS NOT NULL)",
            name="ck_homework_feedback_messages_text_or_photo",
        ),
    )
    op.create_index(
        "ix_homework_feedback_messages_feedback_created",
        "homework_feedback_messages",
        ["feedback_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_homework_feedback_messages_feedback_created",
        table_name="homework_feedback_messages",
    )
    op.drop_table("homework_feedback_messages")
    op.drop_table("homework_feedbacks")
    op.drop_index(
        "ix_homework_submission_images_submission",
        table_name="homework_submission_images",
    )
    op.drop_table("homework_submission_images")
    op.drop_index("ix_homework_submissions_user", table_name="homework_submissions")
    op.drop_index("ix_homework_submissions_task_user", table_name="homework_submissions")
    op.drop_table("homework_submissions")
