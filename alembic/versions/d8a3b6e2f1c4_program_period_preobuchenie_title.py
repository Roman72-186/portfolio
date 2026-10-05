"""program_period_preobuchenie_title

Исправление `c5f2a8d1e7b3`. Та искала этап с названием ровно «Предобучение»,
а на проде он называется «Предобучение 2026-2027» — миграция прошла впустую
(06.10.2026). Владелец просил взять название из АОП, поэтому здесь этап
ищется по началу названия, а период получает название этапа целиком.

Остальное как в `c5f2a8d1e7b3`: период берёт у этапа описание, даты и
видимость, этап вкладывается в него; повторный запуск ничего не дублирует,
этап с уже выбранным периодом не трогается, нет этапа — ничего не делается.

Revision ID: d8a3b6e2f1c4
Revises: c5f2a8d1e7b3
Create Date: 2026-10-06
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8a3b6e2f1c4"
down_revision: Union[str, None] = "c5f2a8d1e7b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Замороженные значения: константы в `app/models/learning_topic.py` могут
# меняться, история миграции — нет.
_PREFIX = "Предобучение"
_KIND_STAGE = "stage"
_KIND_PERIOD = "period"


def _period_id(bind, title: str) -> int | None:
    row = bind.execute(
        sa.text(
            "SELECT id FROM learning_topics"
            " WHERE kind = :kind AND title = :title AND deleted_at IS NULL"
            " ORDER BY id LIMIT 1"
        ),
        {"kind": _KIND_PERIOD, "title": title},
    ).first()
    return row[0] if row is not None else None


def _stages(bind):
    return bind.execute(
        sa.text(
            "SELECT id, title, description, opens_at, ends_at, is_published,"
            " published_at, published_by_id, created_by_id FROM learning_topics"
            " WHERE kind = :kind AND title LIKE :prefix AND deleted_at IS NULL"
            " AND parent_id IS NULL ORDER BY opens_at, id"
        ),
        {"kind": _KIND_STAGE, "prefix": _PREFIX + "%"},
    ).all()


def upgrade() -> None:
    bind = op.get_bind()
    for stage in _stages(bind):
        period_id = _period_id(bind, stage.title)
        if period_id is None:
            now = datetime.now(timezone.utc)
            bind.execute(
                sa.text(
                    "INSERT INTO learning_topics (title, kind, parent_id, description,"
                    " opens_at, ends_at, sort_order, assign_to_all, tariff_restricted,"
                    " is_published, locks_next, published_at, published_by_id,"
                    " created_by_id, created_at, updated_at)"
                    " VALUES (:title, :kind, NULL, :description, :opens_at, :ends_at, 0,"
                    " :true, :false, :is_published, :true, :published_at,"
                    " :published_by_id, :created_by_id, :now, :now)"
                ),
                {
                    "title": stage.title,
                    "kind": _KIND_PERIOD,
                    "description": stage.description,
                    "opens_at": stage.opens_at,
                    "ends_at": stage.ends_at,
                    "is_published": stage.is_published,
                    "published_at": stage.published_at,
                    "published_by_id": stage.published_by_id,
                    "created_by_id": stage.created_by_id,
                    "true": True,
                    "false": False,
                    "now": now,
                },
            )
            period_id = _period_id(bind, stage.title)
        bind.execute(
            sa.text("UPDATE learning_topics SET parent_id = :period_id WHERE id = :stage_id"),
            {"period_id": period_id, "stage_id": stage.id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    periods = bind.execute(
        sa.text(
            "SELECT id FROM learning_topics"
            " WHERE kind = :kind AND title LIKE :prefix AND deleted_at IS NULL"
        ),
        {"kind": _KIND_PERIOD, "prefix": _PREFIX + "%"},
    ).all()
    for (period_id,) in periods:
        bind.execute(
            sa.text("UPDATE learning_topics SET parent_id = NULL WHERE parent_id = :period_id"),
            {"period_id": period_id},
        )
        bind.execute(
            sa.text("DELETE FROM learning_topics WHERE id = :period_id"),
            {"period_id": period_id},
        )
