"""feedback_close_and_rating

ОС, фаза 2 (созвон 30.09.2026, правила подтверждены владельцем 01.10.2026,
план `plans/2026-10-01-apparchi-call-30-09-followup.md`, О10–О26):

- `feedback_closed_at` / `feedback_closed_by_id` у обоих диалогов ОС —
  сдачи в блоке задания (`task_block_feedbacks`) и пробника (`feedbacks`):
  кнопка сотрудника «Завершить ОС» закрывает диалог для обеих сторон;
- `feedback_ratings` — оценка ученика 1–5 с обязательным комментарием, одна
  на диалог (уникальная пара «вид диалога + id»);
- `feedback_rating_images` — до трёх скриншотов к комментарию.

Бэкфилла нет: все существующие диалоги остаются открытыми, оценок нет.

Revision ID: c45616212c1d
Revises: 10711ec88bdb
Create Date: 2026-10-01 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c45616212c1d'
down_revision: Union[str, None] = '10711ec88bdb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DIALOG_TABLES = ("task_block_feedbacks", "feedbacks")


def upgrade() -> None:
    for table in _DIALOG_TABLES:
        op.add_column(
            table, sa.Column("feedback_closed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(
                "feedback_closed_by_id", sa.Integer(),
                sa.ForeignKey(
                    "users.id", ondelete="SET NULL", name=f"fk_{table}_feedback_closed_by",
                ),
                nullable=True,
            ),
        )

    op.create_table(
        "feedback_ratings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dialog_kind", sa.String(length=20), nullable=False),
        sa.Column("dialog_id", sa.Integer(), nullable=False),
        sa.Column(
            "student_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "curator_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("feedback_type", sa.String(length=20), nullable=False),
        sa.Column(
            "task_id", sa.Integer(),
            sa.ForeignKey("tracker_tasks.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("task_title", sa.String(length=300), nullable=True),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dialog_kind", "dialog_id", name="uq_feedback_ratings_dialog"),
        sa.CheckConstraint(
            "score >= 1 AND score <= 5", name="ck_feedback_ratings_score_range",
        ),
        sa.CheckConstraint("length(comment) > 0", name="ck_feedback_ratings_comment"),
    )
    op.create_index("ix_feedback_ratings_curator", "feedback_ratings", ["curator_id"])

    op.create_table(
        "feedback_rating_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "rating_id", sa.Integer(),
            sa.ForeignKey("feedback_ratings.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("image_s3_url", sa.String(length=500), nullable=False),
        sa.Column("image_s3_path", sa.String(length=300), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_feedback_rating_images_rating_id", "feedback_rating_images", ["rating_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_feedback_rating_images_rating_id", table_name="feedback_rating_images")
    op.drop_table("feedback_rating_images")
    op.drop_index("ix_feedback_ratings_curator", table_name="feedback_ratings")
    op.drop_table("feedback_ratings")
    for table in _DIALOG_TABLES:
        op.drop_constraint(f"fk_{table}_feedback_closed_by", table, type_="foreignkey")
        op.drop_column(table, "feedback_closed_by_id")
        op.drop_column(table, "feedback_closed_at")
