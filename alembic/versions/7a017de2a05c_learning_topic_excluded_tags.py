"""Цикл, скрытый от учеников с тегами, и тег «Новенькие» (24.10.2026)

Школа 24.10.2026: 25.10 в годовой курс заходят новички, им — только цикл
адаптации, а недели 1–4 октября не видны (иначе они должники с первого дня).
Новая таблица `learning_topic_excluded_tags` — от кого тема скрыта по тегу
(`video_topics.accessible_topic_ids`). Тег «Новенькие» заводится сразу, чтобы
его можно было выбрать в форме цикла до первого входа новичка; ставит его
`user_management.tag_annual_newcomer`. Данные учеников не трогаются.

Revision ID: 7a017de2a05c
Revises: b5e8d2f1a7c3
Create Date: 2026-10-24 22:36:06.558334

"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a017de2a05c'
down_revision: Union[str, None] = 'b5e8d2f1a7c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEWCOMER_TAG = "Новенькие"


def upgrade() -> None:
    op.create_table(
        "learning_topic_excluded_tags",
        sa.Column(
            "topic_id", sa.Integer(),
            sa.ForeignKey("learning_topics.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "tag_id", sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True,
        ),
    )
    op.create_index(
        "ix_learning_topic_excluded_tags_tag", "learning_topic_excluded_tags", ["tag_id"]
    )

    # Имя тега уникально; сравнение в Python, как `tags.get_or_create_tag`.
    bind = op.get_bind()
    names = [row[0] for row in bind.execute(sa.text("SELECT name FROM tags"))]
    if NEWCOMER_TAG.lower() not in {name.lower() for name in names}:
        bind.execute(
            sa.text("INSERT INTO tags (name, created_at, is_hidden) VALUES (:name, :at, false)"),
            {"name": NEWCOMER_TAG, "at": datetime.now(timezone.utc)},
        )


def downgrade() -> None:
    # Тег «Новенькие» остаётся: на нём уже могут стоять ученики.
    op.drop_index("ix_learning_topic_excluded_tags_tag", "learning_topic_excluded_tags")
    op.drop_table("learning_topic_excluded_tags")
