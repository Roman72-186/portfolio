"""
Управление пользователями суперадмином: soft-delete, блокировка/разблокировка.
"""
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_session
from app.models.audit_log import AuditLog
from app.models.session import Session
from app.models.user import User


def _role_rank(user: User) -> int:
    if user.role:
        return user.role.rank
    return 4 if user.is_admin else 0


def _can_manage_user(actor: User, target: User) -> bool:
    """Only higher-ranked admins can manage another user account."""
    if actor.id == target.id:
        return False
    actor_rank = _role_rank(actor)
    target_rank = _role_rank(target)
    if actor_rank < 4:
        return False
    return actor_rank > target_rank


def _log(db: DBSession, action: str, performed_by_id: int, target_user_id: int, details: str) -> None:
    db.add(AuditLog(
        action=action,
        performed_by_id=performed_by_id,
        target_user_id=target_user_id,
        details=details,
    ))


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
    if not actor or not _can_manage_user(actor, user):
        return False

    now = datetime.now(timezone.utc)
    user.deleted_at = now
    user.is_active = False

    _invalidate_user_sessions(db, target_user_id)
    _log(db, "user_delete", performed_by_id, target_user_id,
         f"Soft-deleted: {user.name} (id={user.id})")
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
    actor = db.query(User).filter(User.id == performed_by_id).first()
    if not actor or not _can_manage_user(actor, user):
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
