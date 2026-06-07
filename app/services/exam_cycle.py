"""Цикл Пробника (план spec/plan_14_05_2026.md + редизайн 2026-05-23).

Цикл = 1 финальная работа Пробника + диалог обратной связи + повторные попытки.
Создаётся при загрузке финальной фото Пробника.
Закрывается, когда админ/суперадмин выставил балл финальной попытке.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee
from app.models.exam_cycle import ExamCycle
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


def close_cycle_if_scored(db: DBSession, work: Work) -> bool:
    """Закрыть цикл, если работа — финальная попытка Пробника с проставленным баллом.

    Вызывается админом/суперадмином после простановки `Work.score`.
    Идемпотентна: если цикл уже закрыт — ничего не делает.

    Returns True если цикл был закрыт этим вызовом.
    """
    if work.work_type != WORK_TYPE_MOCK_EXAM:
        return False
    if not work.is_final:
        return False
    if work.cycle_id is None:
        return False
    if work.score is None:
        return False
    cycle = db.query(ExamCycle).filter(ExamCycle.id == work.cycle_id).first()
    if cycle is None or cycle.closed_at is not None:
        return False
    now = datetime.now(timezone.utc)
    cycle.closed_at = now
    # Закрытие цикла = пробник по предмету считается закрытым → снимаем блокировку,
    # чтобы ученик мог загрузить новый пробник, а админ-UI видел актуальный статус.
    lock = (
        db.query(MockExamLock)
        .filter(
            MockExamLock.user_id == work.user_id,
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
