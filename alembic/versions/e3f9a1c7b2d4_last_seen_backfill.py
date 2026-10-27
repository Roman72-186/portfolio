"""«Был на платформе» до первого пинга — по последнему действию (27.10.2026)

Александрия 27.10.2026: в «Давно не заходили» двое из трёх заходили недавно,
а там стоит 11–12 октября. Миграция `4a83c858efbb` заполнила
`users.last_seen_at` из `last_login_at`, а та дата застывала: пишется только
при новой сессии, сессия продлевается сама. Ученик, который заходил 15–23.10
и с выкатки пинга (24.10) не заходил, так и числится с датой последнего
«нового входа».

Здесь `last_seen_at` поднимается до самого позднего настоящего действия до
первого пинга: сессия (`expires_at` минус срок сессии — продлевается только
запросами к платформе; вход «глазами» не считается), открытие видео, сдача
работы. Журнал событий (`student_activity_events`) не берётся: там 19
выдуманных нажатий 27.10.2026 (`docs/ops-history.md`). Граница — момент
первого пинга (`MIN(user_activity_days.first_seen_at)`): после него дату
ставит сам пинг. Дата только растёт, никогда не уменьшается.

Отката нет: прежние значения и были ошибкой, `downgrade` ничего не делает.

Revision ID: e3f9a1c7b2d4
Revises: 7a017de2a05c
Create Date: 2026-10-27 12:00:00

"""
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f9a1c7b2d4'
down_revision: Union[str, None] = '7a017de2a05c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# `settings.session_ttl_hours` на 27.10.2026. Константой, а не из настроек:
# миграция описывает прошлые данные, а настройка может поменяться.
SESSION_TTL = timedelta(hours=24)

_users = sa.table(
    "users", sa.column("id", sa.Integer), sa.column("last_seen_at", sa.DateTime(timezone=True)),
)
_days = sa.table("user_activity_days", sa.column("first_seen_at", sa.DateTime(timezone=True)))
_sessions = sa.table(
    "sessions", sa.column("user_id", sa.Integer), sa.column("expires_at", sa.DateTime(timezone=True)),
    sa.column("impersonated_by_id", sa.Integer),
)
_views = sa.table(
    "video_view_logs", sa.column("user_id", sa.Integer), sa.column("opened_at", sa.DateTime(timezone=True)),
)
_submissions = sa.table(
    "task_block_submissions", sa.column("user_id", sa.Integer),
    sa.column("submitted_at", sa.DateTime(timezone=True)),
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def upgrade() -> None:
    bind = op.get_bind()
    cutoff = _utc(bind.execute(sa.select(sa.func.min(_days.c.first_seen_at))).scalar())
    if cutoff is None:
        return  # пингов ещё не было — поднимать не от чего

    latest: dict[int, datetime] = {}

    def keep(user_id: int, at: datetime | None) -> None:
        at = _utc(at)
        if at is not None and at < cutoff and (user_id not in latest or at > latest[user_id]):
            latest[user_id] = at

    for user_id, expires_at in bind.execute(
        sa.select(_sessions.c.user_id, sa.func.max(_sessions.c.expires_at))
        .where(_sessions.c.impersonated_by_id.is_(None))
        .group_by(_sessions.c.user_id)
    ):
        if expires_at is not None:
            keep(user_id, _utc(expires_at) - SESSION_TTL)
    for user_id, opened_at in bind.execute(
        sa.select(_views.c.user_id, sa.func.max(_views.c.opened_at))
        .where(_views.c.opened_at < cutoff).group_by(_views.c.user_id)
    ):
        keep(user_id, opened_at)
    for user_id, submitted_at in bind.execute(
        sa.select(_submissions.c.user_id, sa.func.max(_submissions.c.submitted_at))
        .where(_submissions.c.submitted_at < cutoff).group_by(_submissions.c.user_id)
    ):
        keep(user_id, submitted_at)

    for user_id, last_seen_at in bind.execute(sa.select(_users.c.id, _users.c.last_seen_at)):
        at = latest.get(user_id)
        seen = _utc(last_seen_at)
        if at is not None and (seen is None or seen < at):
            bind.execute(_users.update().where(_users.c.id == user_id).values(last_seen_at=at))


def downgrade() -> None:
    pass
