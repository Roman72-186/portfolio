"""program_period_first_semester

Период «1 семестр 2026-2027» над этапами «Октябрь» и «1 семестр_годовой курс
2026-2027». Владелец 06.10.2026: «к периоду привязывается этап» — этапа без
периода не бывает, с этой же выкатки форма этапа без периода не сохраняется.
На проде без периода оставались ровно эти два этапа (57 и 60); заводить
период миграцией при выкатке — решение владельца.

Даты периода — от начала самого раннего этапа до конца самого позднего,
виден ученикам, если виден хоть один из этапов. Повторный запуск ничего не
дублирует: уже заведённый период с тем же названием переиспользуется, этап с
выбранным периодом не трогается, нет таких этапов — ничего не делается.

Revision ID: e4b7c1d9a2f6
Revises: d8a3b6e2f1c4
Create Date: 2026-10-06
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4b7c1d9a2f6"
down_revision: Union[str, None] = "d8a3b6e2f1c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Замороженные значения: константы в `app/models/learning_topic.py` могут
# меняться, история миграции — нет.
_PERIOD_TITLE = "1 семестр 2026-2027"
_STAGE_TITLE = "Октябрь"
_STAGE_PREFIX = "1 семестр"
_KIND_STAGE = "stage"
_KIND_PERIOD = "period"

_STAGES_WHERE = (
    " WHERE kind = :kind AND deleted_at IS NULL AND parent_id IS NULL"
    " AND (title = :title OR title LIKE :prefix)"
)
_STAGES_PARAMS = {"kind": _KIND_STAGE, "title": _STAGE_TITLE, "prefix": _STAGE_PREFIX + "%"}


def _period_id(bind) -> int | None:
    row = bind.execute(
        sa.text(
            "SELECT id FROM learning_topics"
            " WHERE kind = :kind AND title = :title AND deleted_at IS NULL"
            " ORDER BY id LIMIT 1"
        ),
        {"kind": _KIND_PERIOD, "title": _PERIOD_TITLE},
    ).first()
    return row[0] if row is not None else None


def upgrade() -> None:
    bind = op.get_bind()
    stages = bind.execute(
        sa.text(
            "SELECT id, is_published, published_at, published_by_id, created_by_id"
            " FROM learning_topics" + _STAGES_WHERE + " ORDER BY opens_at, id"
        ),
        _STAGES_PARAMS,
    ).all()
    if not stages:
        return

    period_id = _period_id(bind)
    if period_id is None:
        opens_at, ends_at = bind.execute(
            sa.text("SELECT MIN(opens_at), MAX(ends_at) FROM learning_topics" + _STAGES_WHERE),
            _STAGES_PARAMS,
        ).one()
        published = [stage for stage in stages if stage.is_published]
        source = published[0] if published else stages[0]
        now = datetime.now(timezone.utc)
        bind.execute(
            sa.text(
                "INSERT INTO learning_topics (title, kind, parent_id, description,"
                " opens_at, ends_at, sort_order, assign_to_all, tariff_restricted,"
                " is_published, locks_next, published_at, published_by_id,"
                " created_by_id, created_at, updated_at)"
                " VALUES (:title, :kind, NULL, NULL, :opens_at, :ends_at, 0,"
                " :true, :false, :is_published, :true, :published_at,"
                " :published_by_id, :created_by_id, :now, :now)"
            ),
            {
                "title": _PERIOD_TITLE,
                "kind": _KIND_PERIOD,
                "opens_at": opens_at,
                "ends_at": ends_at,
                "is_published": bool(published),
                "published_at": source.published_at if published else None,
                "published_by_id": source.published_by_id if published else None,
                "created_by_id": source.created_by_id,
                "true": True,
                "false": False,
                "now": now,
            },
        )
        period_id = _period_id(bind)

    for stage in stages:
        bind.execute(
            sa.text("UPDATE learning_topics SET parent_id = :period_id WHERE id = :stage_id"),
            {"period_id": period_id, "stage_id": stage.id},
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
