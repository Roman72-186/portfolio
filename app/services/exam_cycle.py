"""Цикл Пробника (план spec/plan_14_05_2026.md + редизайн 2026-05-23).

Цикл = 1 финальная работа Пробника + диалог обратной связи + повторные попытки.
Создаётся при загрузке финальной фото Пробника.
Закрывается, когда админ/суперадмин выставил балл финальной попытке.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from app.models.exam_cycle import ExamCycle
from app.models.work import Work, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.tz import today_msk


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
    if latest is not None and ticket_id is not None and latest.ticket_id == ticket_id:
        return latest, False
    if latest is not None and ticket_id is None and latest.ticket_id is None:
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
    cycle.closed_at = datetime.now(timezone.utc)
    db.flush()
    return True
