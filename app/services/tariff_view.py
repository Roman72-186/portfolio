"""Кнопка «Уверенный максимум» в кабинете куратора (владелец 10.10.2026).

У куратора Алёны свои четверо учеников, а смотреть и сохранять работы ей нужно
у всех учеников тарифа. Это делает отдельный аккаунт просмотра — модератор с
`User.viewer_tariff`. Владелец попросил не держать два окна: в кабинете
куратора кнопка, по ней пароль аккаунта просмотра, и Алёна внутри; обратно —
кнопкой, без нового входа через Telegram.

Устройство — приём входа «глазами» (`cabinet_superadmin.superadmin_impersonate_*`),
но без его плашек, срока 30 минут и адреса просмотра: это не наблюдение за
чужим кабинетом, а вход в свой второй аккаунт.

- Кнопка видна только куратору из `User.viewer_owner_id` аккаунта просмотра.
- Пароль — тот же, что у аккаунта на `/login`; знать его мало — без сессии
  этого куратора кнопки нет.
- Сессия куратора не гасится: её id лежит в подписанной куке возврата. Назад
  пускает, только если кука подписана нами, выдана этому аккаунту просмотра,
  а сессия жива и принадлежит его куратору.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.models.role import Role
from app.models.session import Session
from app.models.user import User
from app.services.rbac import MODERATOR_ROLE_NAME

RETURN_COOKIE = "tariff_view_return"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="tariff-view-v1")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def linked_viewer(db: DBSession, curator_id: int) -> User | None:
    """Действующий аккаунт просмотра, кнопка которого стоит у этого куратора."""
    return (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(
            User.viewer_owner_id == curator_id,
            User.viewer_tariff.isnot(None),
            Role.name == MODERATOR_ROLE_NAME,
            User.is_active == True,  # noqa: E712
            User.deleted_at.is_(None),
        )
        .order_by(User.id)
        .first()
    )


def password_matches(viewer: User, password: str) -> bool:
    if not viewer.password_hash or not password:
        return False
    try:
        return bcrypt.checkpw(password.encode(), viewer.password_hash.encode())
    except ValueError:
        return False


def enter(db: DBSession, viewer: User, curator_session_id: str) -> tuple[Session, str]:
    """Новая сессия аккаунта просмотра и подписанная кука возврата."""
    session = Session(
        user_id=viewer.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    db.flush()
    token = _serializer().dumps({"s": curator_session_id, "v": viewer.id})
    return session, token


def return_session(db: DBSession, token: str | None, viewer_id: int) -> Session | None:
    """Живая сессия куратора, в которую можно вернуть этот аккаунт просмотра,
    или None — кука чужая, подделана, просрочена или куратор уже вышел."""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=settings.session_ttl_hours * 3600)
    except BadData:
        return None
    if not isinstance(data, dict) or data.get("v") != viewer_id:
        return None
    viewer = db.get(User, viewer_id)
    if viewer is None or viewer.viewer_owner_id is None:
        return None
    session = db.get(Session, str(data.get("s") or ""))
    if (
        session is None
        or not session.is_active
        or session.impersonated_by_id is not None
        or session.user_id != viewer.viewer_owner_id
        or _as_utc(session.expires_at) <= datetime.now(timezone.utc)
    ):
        return None
    return session
