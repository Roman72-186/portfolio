"""task block submissions: приём работ прямо в задании

Владелец 07.09.2026: «работы нужно загружать в заданиях». До этого блоки
«Загрузить портфолио» и «Работа на время» только уводили ученика ссылкой на
общий экран `/upload`, работа падала в портфолио (`works`) и с заданием никак
не связывалась.

Две новые таблицы, существующие не трогаются:

1. `task_block_submissions` — одна сдача одного ученика по одному блоку.
   Ключ `(block_id, user_id)`, а не `(task_id, user_id)`, как у домашки: в
   одном задании может стоять несколько блоков приёма работ.
2. `task_block_submission_images` — файлы этой сдачи, до десяти штук.

Тип блока `upload` в перечислениях не заводится: `task_blocks.block_type` —
обычная строка, набор значений держит код (`BLOCK_TYPES`).

Revision ID: 8d1c2b3a4e5f
Revises: 7c3f81ab55d2
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d1c2b3a4e5f'
down_revision: Union[str, None] = '7c3f81ab55d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_block_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reviewed_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "block_id", "user_id", name="uq_task_block_submission_block_user"
        ),
    )
    op.create_index(
        "ix_task_block_submissions_user", "task_block_submissions", ["user_id"]
    )

    op.create_table(
        "task_block_submission_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "submission_id", sa.Integer(),
            sa.ForeignKey("task_block_submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("image_s3_url", sa.String(length=500), nullable=False),
        sa.Column("image_s3_path", sa.String(length=300), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_task_block_submission_images_submission_id",
        "task_block_submission_images", ["submission_id"],
    )
    op.create_index(
        "ix_task_block_submission_images_order",
        "task_block_submission_images", ["submission_id", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_block_submission_images_order",
        table_name="task_block_submission_images",
    )
    op.drop_index(
        "ix_task_block_submission_images_submission_id",
        table_name="task_block_submission_images",
    )
    op.drop_table("task_block_submission_images")
    op.drop_index(
        "ix_task_block_submissions_user", table_name="task_block_submissions"
    )
    op.drop_table("task_block_submissions")
