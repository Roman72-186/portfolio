"""Одноразовая правка данных (2026-06-13).

До перехода на ручное закрытие цикла (см. app/services/exam_cycle.py::close_cycle)
старый close_cycle_if_scored закрывал цикл сразу при выставлении балла — до того,
как куратор успевал дать обратную связь. Эта правка находит циклы, закрытые
СЕГОДНЯ (по МСК — старым автозакрытием, до деплоя ручного close_cycle), у которых
финалке Пробника выставлен балл, но в диалоге ОС нет ни одного сообщения от
куратора/админа/SA, и открывает их заново: closed_at = NULL,
MockExamLock снова ставится в is_locked=True (синхронно с closed_at, см.
debugging_cycle_lock_release_on_close).

После этого куратор/админ/SA даёт ОС и закрывает цикл вручную через
POST /cabinet/feedback/{cycle_id}/close — как и для новых циклов.

Запускать на проде:
    docker exec portfolio-saas-app-1 python scripts/reopen_unfed_back_cycles.py            # dry run
    docker exec portfolio-saas-app-1 python scripts/reopen_unfed_back_cycles.py --apply     # применить
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from app.db.database import SessionLocal
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.mock_exam_lock import MockExamLock
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.feedback import STAFF_ROLES
from app.services.tz import msk_midnight, today_msk

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("reopen_unfed_back_cycles")


def find_candidates(db: DBSession) -> list[tuple[ExamCycle, Work]]:
    candidates: list[tuple[ExamCycle, Work]] = []
    today_start = msk_midnight(today_msk())
    cycles = (
        db.query(ExamCycle)
        .filter(ExamCycle.closed_at.isnot(None), ExamCycle.closed_at >= today_start)
        .all()
    )
    for cycle in cycles:
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
            continue
        has_staff_feedback = (
            db.query(FeedbackMessage.id)
            .join(Feedback, Feedback.id == FeedbackMessage.feedback_id)
            .filter(
                Feedback.work_id == final.id,
                FeedbackMessage.sender_role.in_(STAFF_ROLES),
            )
            .first()
            is not None
        )
        if not has_staff_feedback:
            candidates.append((cycle, final))
    return candidates


def reopen(db: DBSession, candidates: list[tuple[ExamCycle, Work]]) -> None:
    now = datetime.now(timezone.utc)
    for cycle, _final in candidates:
        cycle.closed_at = None
        lock = (
            db.query(MockExamLock)
            .filter(MockExamLock.user_id == cycle.user_id, MockExamLock.subject == cycle.subject)
            .first()
        )
        if lock:
            lock.is_locked = True
            lock.locked_at = now
            lock.unlocked_at = None
            lock.unlocked_by_id = None
        else:
            db.add(MockExamLock(
                user_id=cycle.user_id,
                subject=cycle.subject,
                is_locked=True,
                locked_at=now,
            ))
    db.commit()


def main() -> None:
    apply = "--apply" in sys.argv
    db = SessionLocal()
    try:
        candidates = find_candidates(db)
        if not candidates:
            log.info("Циклов для открытия не найдено.")
            return
        for cycle, final in candidates:
            log.info(
                "cycle_id=%s user_id=%s subject=%r ticket_id=%s work_id=%s score=%s closed_at=%s",
                cycle.id, cycle.user_id, cycle.subject, cycle.ticket_id,
                final.id, final.score, cycle.closed_at,
            )
        log.info("Всего найдено: %d", len(candidates))
        if not apply:
            log.info("Dry run. Запустите с --apply для применения.")
            return
        reopen(db, candidates)
        log.info("Открыто циклов: %d", len(candidates))
    finally:
        db.close()


if __name__ == "__main__":
    main()
