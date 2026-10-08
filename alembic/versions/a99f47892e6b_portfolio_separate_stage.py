"""portfolio_separate_stage

Владелец 08.10.2026: «перенести портфолио [задание 45] в отдельный этап».
05.10.2026 владелец переложил задание «Портфолио» с этапа в «Цикл 1»
(цикл 47), и оно стало жить сроками цикла: «Цикл 1» закончился 26.09, а
новенькие годового курса прошедших циклов не видят
(`ended_before_arrival_filter`) — «Портфолио» им недоступно.

Миграция только раскладывает данные: в периоде «Предобучение 2026-2027»
заводит этап «Портфолио», в нём цикл «Портфолио» и переносит туда задание.
Даты этапа и цикла — как у этапа «Предобучение 2026-2027», с 16.09 по
31.10 по Москве (выбор владельца). Цикл открыт всем (`assign_to_all`, как у
цикла 47) и никого не запирает (`locks_next=False`): обязательных заданий
в нём нет, а порядок сдачи портфолио к остальным циклам не привязан.

Сданное не трогается: отметки (`tracker_task_states`) и работы блоков
ссылаются на задание, а не на цикл.

Повторный запуск ничего не дублирует: живые этап и цикл «Портфолио»
переиспользуются. Нет периода или задания — ничего не делается (чистая
база, тесты на SQLite).

Revision ID: a99f47892e6b
Revises: baf0c1022296
Create Date: 2026-10-08
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a99f47892e6b"
down_revision: Union[str, None] = "baf0c1022296"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Замороженные значения прода на 08.10.2026: константы моделей и данные
# могут меняться, история миграции — нет.
_TASK_ID = 45
_TASK_TITLE = "Портфолио"
_OLD_CYCLE_ID = 47
_PERIOD_TITLE = "Предобучение 2026-2027"
_TITLE = "Портфолио"
_KIND_STAGE = "stage"
_KIND_WEEK = "week"
_KIND_PERIOD = "period"
# 16.09.2026 00:00 и 31.10.2026 23:59:59 по Москве — так же хранит даты
# этап «Предобучение 2026-2027».
_OPENS_AT = datetime(2026, 9, 15, 21, 0, tzinfo=timezone.utc)
_ENDS_AT = datetime(2026, 10, 31, 20, 59, 59, tzinfo=timezone.utc)


def _topic_id(bind, *, kind: str, title: str, parent_id: int | None) -> int | None:
    row = bind.execute(
        sa.text(
            "SELECT id FROM learning_topics"
            " WHERE kind = :kind AND title = :title AND deleted_at IS NULL"
            " AND parent_id IS NOT DISTINCT FROM :parent_id"
            " ORDER BY id LIMIT 1"
        ),
        {"kind": kind, "title": title, "parent_id": parent_id},
    ).first()
    return row[0] if row is not None else None


def _ensure_topic(bind, *, kind: str, parent_id: int, created_by_id: int | None) -> int:
    topic_id = _topic_id(bind, kind=kind, title=_TITLE, parent_id=parent_id)
    if topic_id is not None:
        return topic_id
    now = datetime.now(timezone.utc)
    bind.execute(
        sa.text(
            "INSERT INTO learning_topics (title, kind, parent_id, description,"
            " opens_at, ends_at, sort_order, assign_to_all, tariff_restricted,"
            " is_published, locks_next, published_at, published_by_id,"
            " created_by_id, created_at, updated_at)"
            " VALUES (:title, :kind, :parent_id, NULL, :opens_at, :ends_at, 0,"
            " :true, :false, :true, :false, :now, NULL,"
            " :created_by_id, :now, :now)"
        ),
        {
            "title": _TITLE,
            "kind": kind,
            "parent_id": parent_id,
            "opens_at": _OPENS_AT,
            "ends_at": _ENDS_AT,
            "created_by_id": created_by_id,
            "true": True,
            "false": False,
            "now": now,
        },
    )
    return _topic_id(bind, kind=kind, title=_TITLE, parent_id=parent_id)


def upgrade() -> None:
    bind = op.get_bind()
    period_id = _topic_id(bind, kind=_KIND_PERIOD, title=_PERIOD_TITLE, parent_id=None)
    task = bind.execute(
        sa.text(
            "SELECT id, created_by_id FROM tracker_tasks"
            " WHERE id = :id AND title = :title AND deleted_at IS NULL"
        ),
        {"id": _TASK_ID, "title": _TASK_TITLE},
    ).first()
    if period_id is None or task is None:
        return

    stage_id = _ensure_topic(
        bind, kind=_KIND_STAGE, parent_id=period_id, created_by_id=task.created_by_id
    )
    cycle_id = _ensure_topic(
        bind, kind=_KIND_WEEK, parent_id=stage_id, created_by_id=task.created_by_id
    )
    bind.execute(
        sa.text(
            "UPDATE tracker_tasks SET topic_id = :cycle_id, sort_order = 0,"
            " updated_at = :now WHERE id = :id"
        ),
        {"cycle_id": cycle_id, "id": _TASK_ID, "now": datetime.now(timezone.utc)},
    )


def downgrade() -> None:
    bind = op.get_bind()
    period_id = _topic_id(bind, kind=_KIND_PERIOD, title=_PERIOD_TITLE, parent_id=None)
    if period_id is None:
        return
    stage_id = _topic_id(bind, kind=_KIND_STAGE, title=_TITLE, parent_id=period_id)
    if stage_id is None:
        return
    cycle_id = _topic_id(bind, kind=_KIND_WEEK, title=_TITLE, parent_id=stage_id)
    if cycle_id is not None:
        bind.execute(
            sa.text(
                "UPDATE tracker_tasks SET topic_id = :old_id WHERE id = :id AND topic_id = :cycle_id"
            ),
            {"old_id": _OLD_CYCLE_ID, "id": _TASK_ID, "cycle_id": cycle_id},
        )
        bind.execute(
            sa.text("DELETE FROM learning_topics WHERE id = :id"), {"id": cycle_id}
        )
    bind.execute(sa.text("DELETE FROM learning_topics WHERE id = :id"), {"id": stage_id})
