"""
Управление пользователями: soft-delete, блокировка/разблокировка (суперадмин),
а также аудит-лог смены куратора и тарифа.
"""
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_session
from app.models.audit_log import AuditLog
from app.models.role import Role
from app.models.session import Session
from app.models.user import User


def _role_rank(user: User) -> int:
    if user.role:
        return user.role.rank
    return 4 if user.is_admin else 0


def can_manage_user_by_rank(actor_user_id: int, actor_rank: int, target: User) -> bool:
    """Only higher-ranked admins can manage another user account."""
    if actor_user_id == target.id:
        return False
    target_rank = _role_rank(target)
    if actor_rank < 4:
        return False
    return actor_rank > target_rank


def can_manage_user(actor: User, target: User) -> bool:
    return can_manage_user_by_rank(actor.id, _role_rank(actor), target)


def can_assign_role_rank(actor_rank: int, new_role_rank: int) -> bool:
    return actor_rank >= 4 and new_role_rank < actor_rank


def get_curator_for_assignment(
    db: DBSession,
    curator_id: int,
    *,
    active_only: bool = False,
) -> User | None:
    query = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(User.id == curator_id, Role.rank == 2, User.deleted_at.is_(None))
    )
    if active_only:
        query = query.filter(User.is_active == True)  # noqa: E712
    return query.first()


def _log(db: DBSession, action: str, performed_by_id: int, target_user_id: int, details: str) -> None:
    db.add(AuditLog(
        action=action,
        performed_by_id=performed_by_id,
        target_user_id=target_user_id,
        details=details,
    ))


def log_curator_change(
    db: DBSession,
    performed_by_id: int,
    target_user_id: int,
    old_curator_id: int | None,
    new_curator_id: int | None,
) -> None:
    """Пишет AuditLog(action=curator_assign) при фактической смене куратора.

    Не коммитит — запись уходит вместе с транзакцией вызывающего кода.
    """
    if old_curator_id == new_curator_id:
        return
    _log(db, "curator_assign", performed_by_id, target_user_id,
         f"curator: {old_curator_id if old_curator_id is not None else '—'}"
         f" → {new_curator_id if new_curator_id is not None else '—'}")


def log_tariff_change(
    db: DBSession,
    performed_by_id: int,
    target_user_id: int,
    old_tariff: str | None,
    new_tariff: str | None,
) -> None:
    """Пишет AuditLog(action=tariff_change) при фактической смене тарифа.

    performed_by_id может совпадать с target_user_id (ученик меняет тариф сам).
    Не коммитит — запись уходит вместе с транзакцией вызывающего кода.
    """
    if old_tariff == new_tariff:
        return
    _log(db, "tariff_change", performed_by_id, target_user_id,
         f"tariff: {old_tariff or '—'} → {new_tariff or '—'}")


def _invalidate_user_sessions(db: DBSession, user_id: int) -> None:
    """Деактивирует все активные сессии пользователя и сбрасывает кэш."""
    sessions = (
        db.query(Session)
        .filter(Session.user_id == user_id, Session.is_active == True)
        .all()
    )
    for s in sessions:
        s.is_active = False
        invalidate_session(s.id)


def soft_delete_user(db: DBSession, target_user_id: int, performed_by_id: int) -> bool:
    """
    Soft-delete пользователя: выставляет deleted_at, деактивирует.
    Возвращает False если пользователь не найден или уже удалён.
    Нельзя удалить самого себя.
    """
    user = db.query(User).filter(User.id == target_user_id).first()
    if not user or user.deleted_at is not None:
        return False
    actor = db.query(User).filter(User.id == performed_by_id).first()
    if not actor or not can_manage_user(actor, user):
        return False

    now = datetime.now(timezone.utc)
    user.deleted_at = now
    user.is_active = False

    _invalidate_user_sessions(db, target_user_id)
    _log(db, "user_delete", performed_by_id, target_user_id,
         f"Soft-deleted: {user.name} (id={user.id})")
    db.commit()
    return True


def archive_user(db: DBSession, target_user_id: int, performed_by_id: int, *, commit: bool = True) -> bool:
    """
    Отправляет пользователя в архив: ставит archived_at, гасит is_active,
    выкидывает из активных сессий. Данные (работы, оценки, переписки) не трогаются.
    Возвращает False, если пользователь не найден, уже в архиве, удалён
    или недоступен актору по рангу.
    """
    user = db.query(User).filter(User.id == target_user_id).first()
    if not user or user.archived_at is not None or user.deleted_at is not None:
        return False
    actor = db.query(User).filter(User.id == performed_by_id).first()
    if not actor or not can_manage_user(actor, user):
        return False

    user.archived_at = datetime.now(timezone.utc)
    user.is_active = False

    _invalidate_user_sessions(db, target_user_id)
    _log(db, "user_archive", performed_by_id, target_user_id,
         f"В архив: {user.name} (id={user.id})")
    if commit:
        db.commit()
    return True


def unarchive_user(db: DBSession, target_user_id: int, performed_by_id: int, *, commit: bool = True) -> bool:
    """
    Возвращает пользователя из архива: снимает archived_at и включает is_active.
    Возвращает False, если пользователь не найден, не в архиве, удалён
    или недоступен актору по рангу.
    """
    user = db.query(User).filter(User.id == target_user_id).first()
    if not user or user.archived_at is None or user.deleted_at is not None:
        return False
    actor = db.query(User).filter(User.id == performed_by_id).first()
    if not actor or not can_manage_user(actor, user):
        return False

    user.archived_at = None
    user.is_active = True

    _log(db, "user_unarchive", performed_by_id, target_user_id,
         f"Из архива: {user.name} (id={user.id})")
    if commit:
        db.commit()
    return True


def toggle_user_active(db: DBSession, target_user_id: int, performed_by_id: int) -> bool | None:
    """
    Блокирует или разблокирует пользователя (переключает is_active).
    Нельзя применять к удалённым пользователям и к самому себе.
    Возвращает новое значение is_active или None если операция недопустима.
    """
    user = db.query(User).filter(User.id == target_user_id).first()
    if not user or user.deleted_at is not None:
        return None
    if user.archived_at is not None:
        # У архивного is_active=False по определению. Разблокировка вернула бы его
        # в рабочие списки, оставив в архиве — противоречивое состояние.
        # Возврат из архива делается unarchive_user.
        return None
    actor = db.query(User).filter(User.id == performed_by_id).first()
    if not actor or not can_manage_user(actor, user):
        return None

    if user.is_active:
        new_active = False
    else:
        new_active = True
    user.is_active = new_active

    if not new_active:
        _invalidate_user_sessions(db, target_user_id)

    action = "user_unblock" if new_active else "user_block"
    _log(db, action, performed_by_id, target_user_id,
         f"{'Разблокирован' if new_active else 'Заблокирован'}: {user.name} (id={user.id})")
    db.commit()
    return new_active
