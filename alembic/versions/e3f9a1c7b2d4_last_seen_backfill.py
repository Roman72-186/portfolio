"""«Был на платформе» до первого пинга — по последнему действию (27.10.2026)

Александрия 27.10.2026: в «Давно не заходили» двое из трёх заходили недавно,
а там стоит 11–12 октября. Миграция `4a83c858efbb` заполнила
`users.last_seen_at` из `last_login_at`, а та дата застывала: пишется только
при новой сессии, сессия продлевается сама. Ученик, который заходил 15–23.10
и с выкатки пинга (24.10) не заходил, так и числится с датой последнего
«нового входа».

Здесь `last_seen_at` поднимается до самого позднего настоящего действия до
первого пинга: сессия (`expires_at` минус срок сессии — каждый запрос к
платформе ставит `expires_at = сейчас + срок`, `main.py`, так что разница и
есть последний запрос; вход «глазами» не считается), открытие видео, сдача
работы. Открытие и сдача внутри окна входа «глазами» — действие сотрудника:
до 27.10.2026 открытие видео в чужом кабинете писалось на ученика (у
Захаровой «был» уехал бы на 19.10). Окно — от `impersonate_start` до
следующего `impersonate_stop` по журналу (сессия «глазами» после выхода
удаляется, по таблице сессий окна не найти), без выхода — срок такой сессии. Журнал событий (`student_activity_events`) не берётся: там 19
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

from app.config import settings


# revision identifiers, used by Alembic.
revision: str = 'e3f9a1c7b2d4'
down_revision: Union[str, None] = '7a017de2a05c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Срок сессии — из окружения той базы, где идёт миграция: на проде 27.10.2026
# он 336 часов, а не 24 по умолчанию. С чужим сроком «истекает минус срок»
# уехало бы на 13 дней вперёд от настоящего захода.
SESSION_TTL = timedelta(hours=settings.session_ttl_hours)
# Вход «глазами» без записи о выходе — не дольше срока такой сессии
# (`cabinet_superadmin._IMPERSONATION_TTL_MIN`).
IMPERSONATION_MAX = timedelta(minutes=30)

_users = sa.table(
    "users", sa.column("id", sa.Integer), sa.column("last_seen_at", sa.DateTime(timezone=True)),
)
_days = sa.table("user_activity_days", sa.column("first_seen_at", sa.DateTime(timezone=True)))
_sessions = sa.table(
    "sessions", sa.column("user_id", sa.Integer), sa.column("expires_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)), sa.column("impersonated_by_id", sa.Integer),
)
_audit = sa.table(
    "audit_logs", sa.column("action", sa.String), sa.column("target_user_id", sa.Integer),
    sa.column("created_at", sa.DateTime(timezone=True)),
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
    windows: dict[int, list[tuple[datetime, datetime]]] = {}
    open_since: dict[int, datetime] = {}
    for action, user_id, at in bind.execute(
        sa.select(_audit.c.action, _audit.c.target_user_id, _audit.c.created_at)
        .where(_audit.c.action.in_(("impersonate_start", "impersonate_stop")))
        .order_by(_audit.c.created_at)
    ):
        at = _utc(at)
        if action == "impersonate_start":
            if user_id in open_since:
                start = open_since[user_id]
                windows.setdefault(user_id, []).append((start, start + IMPERSONATION_MAX))
            open_since[user_id] = at
        elif user_id in open_since:
            windows.setdefault(user_id, []).append((open_since.pop(user_id), at))
    for user_id, start in open_since.items():
        windows.setdefault(user_id, []).append((start, start + IMPERSONATION_MAX))
    for user_id, created_at, expires_at in bind.execute(
        sa.select(_sessions.c.user_id, _sessions.c.created_at, _sessions.c.expires_at)
        .where(_sessions.c.impersonated_by_id.isnot(None))
    ):
        if created_at is not None and expires_at is not None:
            windows.setdefault(user_id, []).append((_utc(created_at), _utc(expires_at)))

    def by_student(user_id: int, at: datetime | None) -> bool:
        at = _utc(at)
        return at is not None and not any(start <= at <= end for start, end in windows.get(user_id, []))

    for user_id, opened_at in bind.execute(
        sa.select(_views.c.user_id, _views.c.opened_at).where(_views.c.opened_at < cutoff)
    ):
        if by_student(user_id, opened_at):
            keep(user_id, opened_at)
    for user_id, submitted_at in bind.execute(
        sa.select(_submissions.c.user_id, _submissions.c.submitted_at).where(_submissions.c.submitted_at < cutoff)
    ):
        if by_student(user_id, submitted_at):
            keep(user_id, submitted_at)

    for user_id, last_seen_at in bind.execute(sa.select(_users.c.id, _users.c.last_seen_at)):
        at = latest.get(user_id)
        seen = _utc(last_seen_at)
        if at is not None and (seen is None or seen < at):
            bind.execute(_users.update().where(_users.c.id == user_id).values(last_seen_at=at))


def downgrade() -> None:
    pass
