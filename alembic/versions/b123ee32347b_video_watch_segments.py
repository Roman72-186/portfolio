"""video watch segments — зачёт видео по отрезкам и сеансам (07.10.2026)

Новая таблица `video_watch_segments` и колонка `video_progress.covered_seconds`
(покрытие текущего прохода). План — `plans/2026-10-07-apparchi-видео-отрезки.md`.

Перенос накопленного (развилка 2, владелец 07.10.2026: «согласен»): отрезков
для старых просмотров не восстановить, в базе только сумма. Каждой паре,
у которой в текущем проходе засчитаны секунды, кладётся один отрезок «от
начала ролика до засчитанного, не длиннее ролика» с сеансом `migrated` —
ученик ничего заново не смотрит. Засчитанные ролики остаются засчитанными; у
них текущий проход — то, что набрано после зачёта.

Позицией отрезок не ограничен, хотя в плане стояло «min(засчитано,
позиция)»: на проде 07.10.2026 у 284 незасчитанных пар позиция 0 (ролик
открыт заново, первая отметка записала начало) при 8 минутах просмотра в
среднем, ещё у 138 засчитано больше позиции. Ограничение обнулило бы или
урезало им уже набранное — ровно то, от чего развилка 2 и защищала.

Откат — снос таблицы и колонки; `watched_seconds` и даты зачёта миграция не
трогает, прежнее правило после отката считает по ним как раньше.

Revision ID: b123ee32347b
Revises: 32872b61da2f
Create Date: 2026-10-07 09:53:45.422089

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b123ee32347b'
down_revision: Union[str, None] = '32872b61da2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Засчитано в текущем проходе по прежнему правилу: после зачёта — только
# новый проход (`last_completion_watched_seconds`, 19.09.2026).
_PASS_CREDIT = """
    CASE WHEN completed_at IS NOT NULL
         THEN GREATEST(0, watched_seconds - last_completion_watched_seconds)
         ELSE watched_seconds END
"""
_SEGMENT_END = f"""
    LEAST({_PASS_CREDIT}, COALESCE(duration_seconds, {_PASS_CREDIT}))
"""


def upgrade() -> None:
    op.create_table(
        "video_watch_segments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("video_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_video_watch_segments_user_video", "video_watch_segments",
        ["user_id", "video_id"],
    )
    op.add_column(
        "video_progress",
        sa.Column("covered_seconds", sa.Float(), nullable=False, server_default="0"),
    )
    op.execute(f"""
        INSERT INTO video_watch_segments
            (user_id, video_id, session_id, start_seconds, end_seconds, created_at, updated_at)
        SELECT user_id, video_id, 'migrated', 0, {_SEGMENT_END}, now(), now()
        FROM video_progress
        WHERE {_SEGMENT_END} > 0
    """)
    op.execute(f"""
        UPDATE video_progress SET covered_seconds = {_SEGMENT_END}
        WHERE {_SEGMENT_END} > 0
    """)


def downgrade() -> None:
    op.drop_column("video_progress", "covered_seconds")
    op.drop_index("ix_video_watch_segments_user_video", table_name="video_watch_segments")
    op.drop_table("video_watch_segments")
