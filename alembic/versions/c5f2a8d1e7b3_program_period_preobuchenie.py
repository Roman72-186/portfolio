"""program_period_preobuchenie

Владелец 06.10.2026: структура программы — Период → Этап → Цикл → задания.
Период — новый вид `LearningTopic(kind='period')` над этапом, колонки не
меняются (`kind` — строка, `parent_id` уже есть). Миграция только раскладывает
данные: заводит период «Предобучение» и вкладывает в него этап «Предобучение»
с его циклами — так решил владелец («я, миграцией при выкатке»).

Период берёт у этапа название, описание, даты и видимость. Повторный запуск
ничего не дублирует: живой период «Предобучение» переиспользуется, этап с уже
выбранным периодом не трогается. Нет этапа «Предобучение» — ничего не
делается. Остальные этапы (семестровые) остаются без периода: их владелец
привяжет на вкладке «Этапы», когда заведёт «1 семестр».

Revision ID: c5f2a8d1e7b3
Revises: b3e1c7d2a9f4
Create Date: 2026-10-06
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5f2a8d1e7b3"
down_revision: Union[str, None] = "b3e1c7d2a9f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Замороженные значения: константы в `app/models/learning_topic.py` могут
# меняться, история миграции — нет.
_TITLE = "Предобучение"
_KIND_STAGE = "stage"
_KIND_PERIOD = "period"


def _period_id(bind) -> int | None:
    row = bind.execute(
        sa.text(
            "SELECT id FROM learning_topics"
            " WHERE kind = :kind AND title = :title AND deleted_at IS NULL"
            " ORDER BY id LIMIT 1"
        ),
        {"kind": _KIND_PERIOD, "title": _TITLE},
    ).first()
    return row[0] if row is not None else None


def upgrade() -> None:
    bind = op.get_bind()
    stages = bind.execute(
        sa.text(
            "SELECT id, description, opens_at, ends_at, is_published, published_at,"
            " published_by_id, created_by_id FROM learning_topics"
            " WHERE kind = :kind AND title = :title AND deleted_at IS NULL"
            " AND parent_id IS NULL ORDER BY opens_at, id"
        ),
        {"kind": _KIND_STAGE, "title": _TITLE},
    ).all()
    if not stages:
        return

    period_id = _period_id(bind)
    if period_id is None:
        first = stages[0]
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
                "title": _TITLE,
                "kind": _KIND_PERIOD,
                "description": first.description,
                "opens_at": first.opens_at,
                # Этапов «Предобучение» может оказаться несколько — период
                # накрывает их все.
                "ends_at": max(
                    (s.ends_at for s in stages if s.ends_at is not None), default=None
                ),
                "is_published": first.is_published,
                "published_at": first.published_at,
                "published_by_id": first.published_by_id,
                "created_by_id": first.created_by_id,
                "true": True,
                "false": False,
                "now": now,
            },
        )
        period_id = _period_id(bind)

    bind.execute(
        sa.text(
            "UPDATE learning_topics SET parent_id = :period_id"
            " WHERE kind = :kind AND title = :title AND deleted_at IS NULL"
            " AND parent_id IS NULL"
        ),
        {"period_id": period_id, "kind": _KIND_STAGE, "title": _TITLE},
    )


def downgrade() -> None:
    bind = op.get_bind()
    period_id = _period_id(bind)
    if period_id is None:
        return
    bind.execute(
        sa.text("UPDATE learning_topics SET parent_id = NULL WHERE parent_id = :period_id"),
        {"period_id": period_id},
    )
    bind.execute(
        sa.text("DELETE FROM learning_topics WHERE id = :period_id"),
        {"period_id": period_id},
    )
