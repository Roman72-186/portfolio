"""
Управление пользователями: soft-delete, блокировка/разблокировка (суперадмин),
а также аудит-лог смены куратора и тарифа.
"""
from datetime import datetime, timezone

from sqlalchemy import delete, update
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_session
from app.models.audit_log import AuditLog
from app.models.curator_report import CuratorReport
from app.models.exam_assignment import ExamAssignment
from app.models.exam_cycle import ExamCycle
from app.models.feature_period import FeaturePeriod
from app.models.feedback import Feedback, FeedbackMessage
from app.models.homework_feedback import HomeworkFeedback, HomeworkFeedbackMessage
from app.models.homework_submission import HomeworkSubmission
from app.models.legacy_portfolio_photo import LegacyPortfolioPhoto
from app.models.login_token import LoginToken
from app.models.mock_exam_lock import MockExamLock
from app.models.notification import Notification
from app.models.role import Role
from app.models.session import Session
from app.models.tag import UserTag
from app.models.telegram_link_token import TelegramLinkToken
from app.models.upload_log import UploadLog
from app.models.user import User
from app.models.work import Work

STUDENT_RANK = 1
SUPERADMIN_RANK = 5


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


def _foreign_actor_refs(db: DBSession, target_user_id: int, *,
                         work_ids: list[int], submission_ids: list[int]) -> list[str]:
    """Ищет строки, где target_user_id — актор (куратор/отправитель) в ЧУЖИХ
    диалогах/данных, а не в своих собственных (те и так исчезнут вместе с её
    работами/сдачами домашки). Для роли «ученик» такое в норме невозможно
    (curator_id/created_by предполагают ранг ≥2), но это не повод удалять
    молча — раз нашлось, что-то в данных не соответствует ожиданиям роли,
    и нужно остановиться, а не тихо тянуть за собой чужие диалоги."""
    problems = []

    q = db.query(Feedback.id).filter(Feedback.curator_id == target_user_id)
    if work_ids:
        q = q.filter(~Feedback.work_id.in_(work_ids))
    if q.count():
        problems.append("feedbacks.curator_id (чужие диалоги обратной связи)")

    q = db.query(FeedbackMessage.id).filter(FeedbackMessage.sender_id == target_user_id)
    own_feedback_ids = (
        db.query(Feedback.id).filter(Feedback.work_id.in_(work_ids)).subquery()
        if work_ids else None
    )
    if own_feedback_ids is not None:
        q = q.filter(~FeedbackMessage.feedback_id.in_(db.query(own_feedback_ids.c.id)))
    if q.count():
        problems.append("feedback_messages.sender_id (чужие сообщения обратной связи)")

    q = db.query(HomeworkFeedback.id).filter(HomeworkFeedback.curator_id == target_user_id)
    if submission_ids:
        q = q.filter(~HomeworkFeedback.submission_id.in_(submission_ids))
    if q.count():
        problems.append("homework_feedbacks.curator_id (чужие диалоги по домашке)")

    q = db.query(HomeworkFeedbackMessage.id).filter(HomeworkFeedbackMessage.sender_id == target_user_id)
    own_hw_feedback_ids = (
        db.query(HomeworkFeedback.id).filter(HomeworkFeedback.submission_id.in_(submission_ids)).subquery()
        if submission_ids else None
    )
    if own_hw_feedback_ids is not None:
        q = q.filter(~HomeworkFeedbackMessage.feedback_id.in_(db.query(own_hw_feedback_ids.c.id)))
    if q.count():
        problems.append("homework_feedback_messages.sender_id (чужие сообщения по домашке)")

    if db.query(CuratorReport.id).filter(CuratorReport.curator_id == target_user_id).count():
        problems.append("curator_reports.curator_id (видео-отчёты куратора)")
    if db.query(FeaturePeriod.id).filter(FeaturePeriod.created_by_id == target_user_id).count():
        problems.append("feature_periods.created_by_id (созданные периоды доступности)")
    if db.query(ExamAssignment.id).filter(ExamAssignment.created_by_id == target_user_id).count():
        problems.append("exam_assignments.created_by_id (созданные экзаменационные задания)")

    return problems


def hard_delete_user(
    db: DBSession, target_user_id: int, performed_by_id: int, *, commit: bool = True
) -> tuple[bool, str | None]:
    """Физическое (безвозвратное) удаление ученика и всех его данных: работ,
    фото, попыток пробников, диалогов обратной связи, домашних работ,
    уведомлений, сессий, токенов входа, тегов, истории трекера и видео.

    Только для роли «ученик» (rank=1) — у персонала граф ссылок (кураторские
    отчёты, созданные периоды/задания, диалоги как куратор) намного гуще,
    и удаление такой строки — отдельная, более рискованная задача.

    В отличие от soft_delete_user, после этой операции тот же Telegram-
    аккаунт при следующем входе создаёт СОВЕРШЕННО НОВЫЙ профиль:
    `_upsert_telegram_user` (app/api/auth.py) не находит строку по
    `telegram_chat_id` и заводит нового User с нуля.

    Часть таблиц (mock_exam_attempts, попытки/состояния трекера, видео-
    прогресс, вся ветка домашних заданий и т.д.) снесётся автоматически
    через `ondelete=CASCADE` на уровне БД при удалении строки users — их
    отдельно удалять не нужно. Явно удаляются только таблицы без каскада.

    Файлы в S3 (фото работ, вложения в переписках) НЕ трогаются — по
    решению владельца остаются в хранилище без ссылок из базы, как и у
    прежних точечных прод-операций с данными (см. `clear_dashboard_stats.py`).

    Возвращает `(True, None)` при успехе или `(False, "причина")` при
    отказе. Ничего не пишет в базу при отказе.
    """
    user = db.query(User).filter(User.id == target_user_id).first()
    if not user:
        return False, "Пользователь не найден"
    actor = db.query(User).filter(User.id == performed_by_id).first()
    if not actor or not can_manage_user(actor, user):
        return False, "Недостаточно прав для удаления этого пользователя"
    if _role_rank(actor) < SUPERADMIN_RANK:
        return False, "Полное удаление доступно только суперадмину"
    if _role_rank(user) != STUDENT_RANK:
        return False, "Полное удаление доступно только для роли «ученик»"

    work_ids = [r[0] for r in db.query(Work.id).filter(Work.user_id == target_user_id).all()]
    submission_ids = [
        r[0] for r in db.query(HomeworkSubmission.id)
        .filter(HomeworkSubmission.user_id == target_user_id).all()
    ]

    foreign_refs = _foreign_actor_refs(
        db, target_user_id, work_ids=work_ids, submission_ids=submission_ids
    )
    if foreign_refs:
        return False, (
            "Найдены чужие данные, ссылающиеся на этого пользователя, "
            f"удаление отменено: {', '.join(foreign_refs)}"
        )

    counts = {
        "работы": len(work_ids),
        "диалоги обратной связи": (
            db.query(Feedback.id).filter(Feedback.work_id.in_(work_ids)).count() if work_ids else 0
        ),
        "сдачи домашних работ": len(submission_ids),
        "экзаменационные циклы": db.query(ExamCycle.id).filter(ExamCycle.user_id == target_user_id).count(),
        "уведомления": db.query(Notification.id).filter(Notification.user_id == target_user_id).count(),
    }
    name = user.name
    telegram_chat_id = user.telegram_chat_id

    # Кто-то другой может считать её своим куратором/оценщиком — обнулить,
    # не трогая при этом чужие строки целиком.
    db.execute(update(User).where(User.curator_id == target_user_id).values(curator_id=None))
    db.execute(update(User).where(User.portfolio_before_scored_by_id == target_user_id)
               .values(portfolio_before_scored_by_id=None))
    db.execute(update(User).where(User.portfolio_after_scored_by_id == target_user_id)
               .values(portfolio_after_scored_by_id=None))
    db.execute(update(Work).where(Work.scored_by_id == target_user_id).values(scored_by_id=None))
    db.execute(update(Work).where(Work.viewed_by_id == target_user_id).values(viewed_by_id=None))
    db.execute(update(Work).where(Work.uploaded_by_id == target_user_id).values(uploaded_by_id=None))
    db.execute(update(ExamCycle).where(ExamCycle.viewed_by_id == target_user_id).values(viewed_by_id=None))
    db.execute(update(MockExamLock).where(MockExamLock.unlocked_by_id == target_user_id)
               .values(unlocked_by_id=None))
    db.execute(update(CuratorReport).where(CuratorReport.viewed_by_id == target_user_id)
               .values(viewed_by_id=None))
    db.execute(update(Session).where(Session.impersonated_by_id == target_user_id)
               .values(impersonated_by_id=None))

    _invalidate_user_sessions(db, target_user_id)

    # Дети раньше родителей: feedbacks.work_id и works.cycle_id без ondelete.
    db.execute(update(Work).where(Work.user_id == target_user_id).values(parent_work_id=None))
    if work_ids:
        db.execute(delete(Feedback).where(Feedback.work_id.in_(work_ids)))
    db.execute(delete(Work).where(Work.user_id == target_user_id))
    db.execute(delete(ExamCycle).where(ExamCycle.user_id == target_user_id))
    db.execute(delete(Notification).where(Notification.user_id == target_user_id))
    db.execute(delete(LegacyPortfolioPhoto).where(LegacyPortfolioPhoto.user_id == target_user_id))
    db.execute(delete(UserTag).where(UserTag.user_id == target_user_id))
    db.execute(delete(UploadLog).where(UploadLog.user_id == target_user_id))
    db.execute(delete(LoginToken).where(LoginToken.user_id == target_user_id))
    db.execute(delete(TelegramLinkToken).where(TelegramLinkToken.user_id == target_user_id))
    db.execute(delete(MockExamLock).where(MockExamLock.user_id == target_user_id))
    db.execute(delete(Session).where(Session.user_id == target_user_id))

    # audit_logs.performed_by_id — NOT NULL и без ondelete: ученик мог сам
    # менять себе тариф (log_tariff_change), это его собственный след, не
    # чужой — удаляется целиком, а не обнуляется.
    db.execute(delete(AuditLog).where(
        (AuditLog.target_user_id == target_user_id) | (AuditLog.performed_by_id == target_user_id)
    ))

    details = (
        f"Полное удаление: {name} (id={user.id}, telegram_chat_id={telegram_chat_id or '—'}). "
        + ", ".join(f"{k}: {v}" for k, v in counts.items())
    )
    _log(db, "user_hard_delete", performed_by_id, None, details)

    db.execute(delete(User).where(User.id == target_user_id))

    if commit:
        db.commit()
    return True, None


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
