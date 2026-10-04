"""schedule_event_types

Владелец 04.10.2026: в календаре дайджеста тип события и цвет — одна связка,
типы и цвета настраивает Главный преподаватель. До этого тип был одной из
четырёх строк в коде (`schedule_events.kind`), а цвет выбирался у каждого
события отдельно (`schedule_events.color`, 01.10.2026).

- новая таблица `schedule_event_types`, засеяна семью типами (шесть из списка
  владельца + «Общий эфир», он оставлен седьмым по решению 04.10.2026);
- `schedule_events.type_id` заполняется по старому `kind`:
  deadline → «Дедлайн», lesson → «Занятие», mock_exam → «Пробник»,
  broadcast → «Общий эфир»;
- `kind` и `color` удаляются: цвет события теперь только от типа, второй
  источник правды разошёлся бы с первым. Индивидуальный цвет старых событий
  теряется — это и есть просьба владельца.

Список типов здесь — замороженная копия: `app/models/tracker.py::DEFAULT_EVENT_TYPES`
может меняться, история миграции — нет.

Revision ID: 0c4e9d2b7a61
Revises: a3c7e9b1d5f2
Create Date: 2026-10-04
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0c4e9d2b7a61"
down_revision: Union[str, None] = "a3c7e9b1d5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (название, цвет, стиль, прежний kind или None)
_SEED = (
    ("Публикация уроков и заданий", "sky", "fill", None),
    ("Дедлайн", "pink", "fill", "deadline"),
    ("Обратная связь", "mint", "fill", None),
    ("Пробник", "teal", "ring", "mock_exam"),
    ("Занятие", "violet", "fill", "lesson"),
    ("Период сдачи контрольных", "coral", "ring", None),
    ("Общий эфир", "orange", "fill", "broadcast"),
)

# Цвет для отката: старая палитра знала пять ключей.
_OLD_COLORS = {"orange", "yellow", "sky", "pink", "purple"}


def upgrade() -> None:
    types = op.create_table(
        "schedule_event_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("color", sa.String(16), nullable=False),
        sa.Column("style", sa.String(8), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    now = datetime.now(timezone.utc)
    op.bulk_insert(types, [
        {"name": name, "color": color, "style": style, "sort_order": index, "created_at": now}
        for index, (name, color, style, _kind) in enumerate(_SEED)
    ])

    op.add_column("schedule_events", sa.Column("type_id", sa.Integer(), nullable=True))

    bind = op.get_bind()
    ids = dict(bind.execute(sa.text("SELECT name, id FROM schedule_event_types")).all())
    for name, _color, _style, kind in _SEED:
        if kind is None:
            continue
        bind.execute(
            sa.text("UPDATE schedule_events SET type_id = :type_id WHERE kind = :kind"),
            {"type_id": ids[name], "kind": kind},
        )
    # Строк с иным kind форма не пропускала, но данные старше проверки
    # неизвестны: такие события становятся дедлайном, а не теряются.
    bind.execute(
        sa.text("UPDATE schedule_events SET type_id = :type_id WHERE type_id IS NULL"),
        {"type_id": ids["Дедлайн"]},
    )

    with op.batch_alter_table("schedule_events") as batch:
        batch.alter_column("type_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_schedule_events_type_id", "schedule_event_types",
            ["type_id"], ["id"], ondelete="RESTRICT",
        )
        batch.create_index("ix_schedule_events_type_id", ["type_id"])
        batch.drop_column("kind")
        batch.drop_column("color")


def downgrade() -> None:
    with op.batch_alter_table("schedule_events") as batch:
        batch.add_column(sa.Column("kind", sa.String(20), nullable=True))
        batch.add_column(
            sa.Column("color", sa.String(16), nullable=False, server_default="purple")
        )

    bind = op.get_bind()
    by_name = {name: kind for name, _c, _s, kind in _SEED if kind}
    rows = bind.execute(sa.text(
        "SELECT e.id, t.name, t.color FROM schedule_events e "
        "JOIN schedule_event_types t ON t.id = e.type_id"
    )).all()
    for event_id, type_name, type_color in rows:
        bind.execute(
            sa.text("UPDATE schedule_events SET kind = :kind, color = :color WHERE id = :id"),
            {
                "kind": by_name.get(type_name, "deadline"),
                "color": type_color if type_color in _OLD_COLORS else "purple",
                "id": event_id,
            },
        )

    with op.batch_alter_table("schedule_events") as batch:
        batch.alter_column("kind", existing_type=sa.String(20), nullable=False)
        batch.drop_index("ix_schedule_events_type_id")
        batch.drop_constraint("fk_schedule_events_type_id", type_="foreignkey")
        batch.drop_column("type_id")
    op.drop_table("schedule_event_types")
