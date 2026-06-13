"""Цикл Пробника (план spec/plan_14_05_2026.md + редизайн 2026-05-23).

Цикл = 1 финальная работа Пробника + диалог обратной связи + повторные попытки.
Создаётся при загрузке финальной фото Пробника.
Закрывается вручную куратором/админом/SA после обратной связи (см. close_cycle) —
балл проставляется раньше и закрытие цикла его не требует автоматически.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage, FeedbackPhoto
from app.models.mock_exam_lock import MockExamLock
from app.models.work import Work, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.tz import today_msk


def get_active_ticket(db: DBSession, user_id: int, subject: str) -> ExamTicket | None:
    """Единый резолвер активного билета по предмету (source of truth).

    Самый свежий опубликованный билет в окне дат, назначенный всем или этому
    пользователю. Используется и бэкенд-блоком сдачи, и UI-дизейблом кнопки —
    оба обязаны видеть ОДИН и тот же билет, иначе кнопка и 409 рассинхронятся.
    Порядок newest-first важен только при пересекающихся билетах одного предмета.
    """
    today = today_msk()
    return (
        db.query(ExamTicket)
        .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
        .filter(
            ExamAssignment.status == "published",
            ExamAssignment.subject == subject,
            ExamTicket.start_date <= today,
            ExamTicket.end_date >= today,
            or_(
                ExamTicket.assign_to_all.is_(True),
                ExamTicket.id.in_(
                    db.query(ExamTicketAssignee.ticket_id)
                    .filter(ExamTicketAssignee.user_id == user_id)
                    .scalar_subquery()
                ),
            ),
        )
        .order_by(ExamTicket.start_date.desc(), ExamTicket.id.desc())
        .first()
    )


def has_cycle_for_ticket(db: DBSession, user_id: int, subject: str, ticket_id: int) -> bool:
    """True если по этому билету уже есть цикл Пробника (открытый ИЛИ закрытый).

    Source of truth для правила «одна сдача на билет»: пробник по предмету закрыт
    с момента первой сдачи и до выдачи СЛЕДУЮЩЕГО билета (нового ticket_id).
    """
    return (
        db.query(ExamCycle.id)
        .filter(
            ExamCycle.user_id == user_id,
            ExamCycle.subject == subject,
            ExamCycle.ticket_id == ticket_id,
        )
        .first()
        is not None
    )


def has_submitted_for_ticket(db: DBSession, user_id: int, subject: str, ticket_id: int) -> bool:
    """True если по этому билету уже загружено финальное фото Пробника.

    В отличие от has_cycle_for_ticket проверяет наличие Work.is_final=True,
    а не просто факт существования цикла. Нужно, потому что цикл теперь
    создаётся при загрузке этапных фото — раньше, чем финальное.

    Финал с needs_revision=True (отправлен «на доработку») не считается сдачей —
    это позволяет _overwrite_final перезаписать его новым фото.
    """
    return (
        db.query(ExamCycle.id)
        .join(Work, Work.cycle_id == ExamCycle.id)
        .filter(
            ExamCycle.user_id == user_id,
            ExamCycle.subject == subject,
            ExamCycle.ticket_id == ticket_id,
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.is_final == True,  # noqa: E712
            Work.needs_revision == False,  # noqa: E712
        )
        .first()
        is not None
    )


def find_latest_cycle(db: DBSession, user_id: int, subject: str) -> ExamCycle | None:
    """Последний цикл пользователя по предмету (DESC по started_at, id)."""
    return (
        db.query(ExamCycle)
        .filter(ExamCycle.user_id == user_id, ExamCycle.subject == subject)
        .order_by(ExamCycle.started_at.desc(), ExamCycle.id.desc())
        .first()
    )


def get_or_create_cycle_for_probnik(
    db: DBSession,
    *,
    user_id: int,
    subject: str,
    ticket_id: int | None,
) -> tuple[ExamCycle, bool]:
    """Цикл для финальной Пробника.

    Логика: если последний цикл этого пользователя+предмета привязан к тому же
    билету — повторная попытка по тому же билету → возвращаем существующий.
    Иначе создаём новый цикл.

    Returns (cycle, created).
    """
    latest = find_latest_cycle(db, user_id, subject)
    # Переиспользуем только ОТКРЫТЫЙ цикл (closed_at IS NULL). Закрытый цикл —
    # завершённая попытка с обратной связью: новая загрузка должна стартовать
    # новый цикл, а не доклеивать финалку к закрытому.
    if latest is not None and latest.closed_at is None:
        if ticket_id is not None and latest.ticket_id == ticket_id:
            return latest, False
        if ticket_id is None and latest.ticket_id is None:
            return latest, False
    cycle = ExamCycle(
        user_id=user_id,
        subject=subject,
        ticket_id=ticket_id,
        started_at=today_msk(),
    )
    db.add(cycle)
    db.flush()
    return cycle, True


def next_attempt_number(db: DBSession, *, cycle_id: int, work_type: str) -> int:
    """Следующий attempt_number в рамках цикла + типа работы.

    Считаем только финальные (is_final=true) этого work_type в цикле.
    Per-type — mock_exam и retake нумеруются раздельно.
    """
    count = (
        db.query(Work)
        .filter(
            Work.cycle_id == cycle_id,
            Work.work_type == work_type,
            Work.is_final == True,  # noqa: E712
        )
        .count()
    )
    return count + 1


def get_required_cycle_for_retake(
    db: DBSession, user_id: int, subject: str
) -> ExamCycle | None:
    """Цикл для Отработки. None → клиент должен показать «Сначала пройди Пробник»."""
    return find_latest_cycle(db, user_id, subject)


def has_open_cycles(db: DBSession, user_id: int) -> bool:
    """True если у пользователя есть хотя бы один незакрытый цикл."""
    return db.query(
        db.query(ExamCycle)
        .filter(ExamCycle.user_id == user_id, ExamCycle.closed_at.is_(None))
        .exists()
    ).scalar()


def delete_open_cycle(db: DBSession, cycle: ExamCycle) -> list[str]:
    """Полностью удалить ОТКРЫТЫЙ цикл Пробника и все связанные данные.

    Сценарий: ученик ошибся при отправке, суперадмин удаляет цикл. Удаляются
    works (финалы + этапы), диалог обратной связи (feedbacks + messages + legacy
    photos), снимается блокировка пробника по предмету (иначе ученик не сможет
    пересдать) и сам цикл. Возвращает S3-пути для последующей best-effort очистки.

    Каскад выполняется ЯВНО и в порядке зависимостей: тестовый SQLite не включает
    PRAGMA foreign_keys, а в Postgres `feedbacks.work_id` и `works.parent_work_id`
    — обычные FK без ON DELETE CASCADE. Полагаться на БД нельзя; кроме того, per-row
    `db.delete()` мог бы удалить родительскую работу раньше дочерней (retake →
    parent_work_id) и упасть на FK в проде. Поэтому bulk-delete по уровням.
    """
    work_ids = [row[0] for row in db.query(Work.id).filter(Work.cycle_id == cycle.id).all()]
    s3_paths: list[str] = []
    feedback_ids: list[int] = []
    if work_ids:
        s3_paths += [
            p for (p,) in db.query(Work.s3_path)
            .filter(Work.cycle_id == cycle.id, Work.s3_path.isnot(None)).all()
        ]
        feedback_ids = [
            row[0] for row in db.query(Feedback.id).filter(Feedback.work_id.in_(work_ids)).all()
        ]
    if feedback_ids:
        s3_paths += [
            p for (p,) in db.query(FeedbackMessage.photo_s3_path)
            .filter(
                FeedbackMessage.feedback_id.in_(feedback_ids),
                FeedbackMessage.photo_s3_path.isnot(None),
            ).all()
        ]
        s3_paths += [
            p for (p,) in db.query(FeedbackPhoto.s3_path)
            .filter(FeedbackPhoto.feedback_id.in_(feedback_ids)).all()
        ]
        db.query(FeedbackMessage).filter(
            FeedbackMessage.feedback_id.in_(feedback_ids)
        ).delete(synchronize_session=False)
        db.query(FeedbackPhoto).filter(
            FeedbackPhoto.feedback_id.in_(feedback_ids)
        ).delete(synchronize_session=False)
        db.query(Feedback).filter(
            Feedback.id.in_(feedback_ids)
        ).delete(synchronize_session=False)
    if work_ids:
        db.query(Work).filter(Work.cycle_id == cycle.id).delete(synchronize_session=False)
    # Снять блокировку пробника по предмету — как в close_cycle.
    lock = (
        db.query(MockExamLock)
        .filter(
            MockExamLock.user_id == cycle.user_id,
            MockExamLock.subject == cycle.subject,
            MockExamLock.is_locked == True,  # noqa: E712
        )
        .first()
    )
    if lock:
        lock.is_locked = False
        lock.unlocked_at = datetime.now(timezone.utc)
    db.query(ExamCycle).filter(ExamCycle.id == cycle.id).delete(synchronize_session=False)
    db.flush()
    return s3_paths


def close_cycle(db: DBSession, cycle: ExamCycle) -> bool:
    """Закрыть цикл вручную (куратор/админ/SA после обратной связи).

    Требует, чтобы финальная попытка Пробника в цикле уже имела выставленный
    балл — балл ставится раньше, ОС даётся после, закрытие — последний шаг.
    Идемпотентна: если цикл уже закрыт — ничего не делает.

    Returns True если цикл был закрыт этим вызовом.
    """
    if cycle.closed_at is not None:
        return False
    final = (
        db.query(Work)
        .filter(
            Work.cycle_id == cycle.id,
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.is_final == True,  # noqa: E712
        )
        .order_by(Work.attempt_number.desc(), Work.id.desc())
        .first()
    )
    if final is None or final.score is None:
        return False
    now = datetime.now(timezone.utc)
    cycle.closed_at = now
    # Закрытие цикла = пробник по предмету считается закрытым → снимаем блокировку,
    # чтобы ученик мог загрузить новый пробник, а админ-UI видел актуальный статус.
    lock = (
        db.query(MockExamLock)
        .filter(
            MockExamLock.user_id == cycle.user_id,
            MockExamLock.subject == cycle.subject,
            MockExamLock.is_locked == True,  # noqa: E712
        )
        .first()
    )
    if lock:
        lock.is_locked = False
        lock.unlocked_at = now
    db.flush()
    return True


def reopen_cycle(db: DBSession, cycle: ExamCycle) -> bool:
    """Переоткрыть закрытый цикл (SA, если закрыли по ошибке).

    Точное зеркало close_cycle: сбрасываем closed_at и возвращаем блокировку
    предмета (close снимал её). Балл финалки не трогаем — появление работы в
    Портфолио привязано к score, а не к closed_at (см. cabinet_student
    _mock_exam_finals: closed_only фильтрует по Work.score), поэтому переоткрытие
    не выдёргивает работу из Портфолио.

    Блокировку только восстанавливаем на существующей строке (не создаём новую) —
    у оценённого финала строка lock всегда уже есть (см. upload final flow).
    Идемпотентна: если цикл уже открыт — ничего не делает.

    Returns True если цикл был переоткрыт этим вызовом.
    """
    if cycle.closed_at is None:
        return False
    now = datetime.now(timezone.utc)
    cycle.closed_at = None
    # Переоткрытие отменяет любой висящий возврат на правку: иначе флаг держал бы
    # уже открытый цикл в списке как «на изменении».
    cycle.revision_requested_at = None
    lock = (
        db.query(MockExamLock)
        .filter(
            MockExamLock.user_id == cycle.user_id,
            MockExamLock.subject == cycle.subject,
        )
        .first()
    )
    if lock:
        lock.is_locked = True
        lock.locked_at = now
        lock.unlocked_at = None
        lock.unlocked_by_id = None
    db.flush()
    return True


def request_curator_revision(db: DBSession, cycle: ExamCycle) -> int | None:
    """SA возвращает цикл (любой — открытый или закрытый) автору ОС на правку.

    Статус цикла не меняем (closed_at/балл/портфолио/блокировка не трогаются) —
    ставим только revision_requested_at. Возвращает id куратора-автора последней
    ОС (для адресной видимости/доступа) или None, если в цикле нет обратной связи
    (нечего править — вызывающий отдаёт 400).
    """
    fb = (
        db.query(Feedback)
        .join(Work, Feedback.work_id == Work.id)
        .filter(Work.cycle_id == cycle.id)
        .order_by(Feedback.id.desc())
        .first()
    )
    if fb is None:
        return None
    cycle.revision_requested_at = datetime.now(timezone.utc)
    db.flush()
    return fb.curator_id


def finish_curator_revision(db: DBSession, cycle: ExamCycle) -> bool:
    """Куратор завершил правку — снимаем флаг «на изменении». Цикл уже закрыт.

    Returns True если флаг был снят этим вызовом.
    """
    if cycle.revision_requested_at is None:
        return False
    cycle.revision_requested_at = None
    db.flush()
    return True
