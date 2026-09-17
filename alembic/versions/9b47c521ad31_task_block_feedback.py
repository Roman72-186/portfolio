"""Оценка и диалог обратной связи по сдаче блока задания.

Revision ID: 9b47c521ad31
Revises: f05d2ddfab8c
Create Date: 2026-09-17
"""

from alembic import op
import sqlalchemy as sa


revision = "9b47c521ad31"
down_revision = "f05d2ddfab8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_block_submissions", sa.Column("score", sa.Numeric(5, 2), nullable=True))
    op.add_column(
        "task_block_submissions", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "task_block_submissions", sa.Column("scored_by_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_task_block_submissions_scored_by", "task_block_submissions", "users",
        ["scored_by_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_task_block_submissions_score_range", "task_block_submissions",
        "score IS NULL OR (score >= 0 AND score <= 100)",
    )
    op.create_table(
        "task_block_feedbacks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "submission_id", sa.Integer(),
            sa.ForeignKey("task_block_submissions.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column("curator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "task_block_feedback_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "feedback_id", sa.Integer(),
            sa.ForeignKey("task_block_feedbacks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("sender_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sender_role", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("photo_s3_path", sa.String(500), nullable=True),
        sa.Column("photo_s3_url", sa.String(500), nullable=True),
        sa.Column("video_url", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(text IS NOT NULL AND length(text) > 0) OR (photo_s3_url IS NOT NULL) "
            "OR (video_url IS NOT NULL)",
            name="ck_task_block_feedback_messages_content",
        ),
    )
    op.create_index(
        "ix_task_block_feedback_messages_feedback_created",
        "task_block_feedback_messages", ["feedback_id", "created_at"],
    )
    op.add_column(
        "notifications", sa.Column("task_block_submission_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_notifications_task_block_submission", "notifications", "task_block_submissions",
        ["task_block_submission_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_notifications_task_block_submission", "notifications", type_="foreignkey")
    op.drop_column("notifications", "task_block_submission_id")
    op.drop_index(
        "ix_task_block_feedback_messages_feedback_created",
        table_name="task_block_feedback_messages",
    )
    op.drop_table("task_block_feedback_messages")
    op.drop_table("task_block_feedbacks")
    op.drop_constraint(
        "ck_task_block_submissions_score_range", "task_block_submissions", type_="check"
    )
    op.drop_constraint(
        "fk_task_block_submissions_scored_by", "task_block_submissions", type_="foreignkey"
    )
    op.drop_column("task_block_submissions", "scored_by_id")
    op.drop_column("task_block_submissions", "scored_at")
    op.drop_column("task_block_submissions", "score")
