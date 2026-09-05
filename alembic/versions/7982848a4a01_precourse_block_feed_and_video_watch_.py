"""precourse block feed and video watch guard

Владелец 05.09.2026 — созвон 03.09 про предобучение, разбор
plans/2026-09-04-apparchi-precourse-block-feed-implementation-plan.md.
Только схема этого захода (владелец подтвердил тарифы и порог перемотки,
остальные открытые вопросы плана — §7 — не решены и здесь не закрываются).

1. `task_blocks.is_required`/`subject` — обязательность и предмет на уровне
   отдельного блока, не всей задачи. `is_required` default False (в отличие
   от `TrackerTask.is_required`, там True) — старые блоки уже отрисованы в
   основном обучении, True по умолчанию заставил бы их что-то блокировать.
2. `task_block_tariffs` — per-блок тарифный гейт, зеркало `TrackerTaskTag`.
   Канонические значения тарифа по-прежнему из `app.constants.TARIFFS`, эта
   таблица только ссылается на них строкой — переименование тарифа остаётся
   правкой `constants.py`, а не миграцией.
3. `task_block_states` — статус блока у ученика, зеркало `TrackerTaskState`.
   `TaskBlockResponse` для этого не годится (заполнение вопросов всего
   задания разом, не одного блока).
4. `task_block_options.requires_text` / `task_block_answer_options.text` —
   раскрывающееся текстовое поле под конкретным выбранным вариантом опроса.
5. `video_progress.watched_seconds` — накопленное реальное время просмотра,
   отдельно от `position_seconds` (которую перемотка ставит одним
   движением). Защита от перемотки, владелец 05.09.2026: «следить, что
   длина видео на любой скорости равна времени просмотра, с погрешностью
   30-40 сек» — реализовано как `VIDEO_WATCH_TOLERANCE_SECONDS = 35` в
   `app/constants.py`, независимо от скорости воспроизведения. Новую колонку
   для незавершённых просмотров (`completed_at IS NULL`) бэкфиллим значением
   `position_seconds` — иначе ученик, который на момент деплоя досмотрел
   почти до конца, обнулился бы и должен был бы смотреть почти всё заново.

Revision ID: 7982848a4a01
Revises: 744b7e5e4961
Create Date: 2026-09-05 20:27:28.333143

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7982848a4a01'
down_revision: Union[str, None] = '744b7e5e4961'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Обязательность и предмет на уровне блока ──────────────────────────
    op.add_column(
        "task_blocks",
        sa.Column(
            "is_required", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "task_blocks",
        sa.Column("subject", sa.String(length=50), nullable=True),
    )

    # ── 2. Per-блок тарифный гейт ─────────────────────────────────────────────
    op.create_table(
        "task_block_tariffs",
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("tariff", sa.String(length=50), primary_key=True),
    )

    # ── 3. Состояние блока у ученика ──────────────────────────────────────────
    op.create_table(
        "task_block_states",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "block_id", sa.Integer(),
            sa.ForeignKey("task_blocks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completed_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("completion_source", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("block_id", "user_id", name="uq_task_block_state_block_user"),
    )
    # Отдельный индекс по block_id не нужен: его покрывает как левый префикс
    # уникальный индекс (block_id, user_id) выше — та же экономия, что у
    # TrackerTaskState (индекс там тоже только по user_id/status).
    op.create_index("ix_task_block_states_user", "task_block_states", ["user_id"])

    # ── 4. Условная логика опроса ─────────────────────────────────────────────
    op.add_column(
        "task_block_options",
        sa.Column(
            "requires_text", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "task_block_answer_options",
        sa.Column("text", sa.Text(), nullable=True),
    )

    # ── 5. Реальное время просмотра — защита от перемотки ────────────────────
    op.add_column(
        "video_progress",
        sa.Column(
            "watched_seconds", sa.Float(), nullable=False,
            server_default="0",
        ),
    )
    # Бэкфилл только для незавершённых просмотров (ревью 05.09.2026, найдено
    # после первой реализации): без него ученик, который на момент деплоя был
    # на 590 из 600 секунд, после миграции получал watched_seconds=0 и должен
    # был бы заново набрать почти всю длительность реальным временем, хотя
    # честно досматривал до этого. Позиция — не идеальный прокси реального
    # времени (её тоже можно было перемотать), но для уже завершённых
    # просмотров (completed_at не пуст) трогать нечего — там значение и так
    # неважно, а для тех, кто ещё смотрит, это разумная поблажка на переход,
    # а не постоянная лазейка: следующая же перемотка после миграции ловится
    # как обычно.
    op.execute(
        """
        UPDATE video_progress
        SET watched_seconds = position_seconds
        WHERE completed_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("video_progress", "watched_seconds")

    op.drop_column("task_block_answer_options", "text")
    op.drop_column("task_block_options", "requires_text")

    op.drop_index("ix_task_block_states_user", table_name="task_block_states")
    op.drop_table("task_block_states")

    op.drop_table("task_block_tariffs")

    op.drop_column("task_blocks", "subject")
    op.drop_column("task_blocks", "is_required")
