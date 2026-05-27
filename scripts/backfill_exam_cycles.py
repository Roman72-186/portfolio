"""Backfill ExamCycle для существующих mock_exam/retake работ.

Для каждой пары (user_id, subject), у которой есть mock_exam или retake работы
без cycle_id, создаём один ExamCycle (started_at = min created_at) и
привязываем все эти работы к нему.

Запускать на проде:
    docker exec portfolio-saas-app-1 python scripts/backfill_exam_cycles.py
"""
from __future__ import annotations

import logging
import sys
from collections import defaultdict

from sqlalchemy.orm import Session as DBSession

# Подключаемся к БД через приложение
from app.db.database import SessionLocal
from app.models.exam_cycle import ExamCycle
from app.models.work import Work, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("backfill")


def backfill(db: DBSession, *, dry_run: bool = False) -> dict:
    works = (
        db.query(Work)
        .filter(
            Work.work_type.in_([WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE]),
            Work.cycle_id.is_(None),
            Work.user_id.isnot(None),
            Work.subject.isnot(None),
            Work.status == "success",
        )
        .order_by(Work.created_at)
        .all()
    )
    log.info("Found %d mock_exam/retake works without cycle_id", len(works))

    by_pair: dict[tuple[int, str], list[Work]] = defaultdict(list)
    for w in works:
        by_pair[(w.user_id, w.subject)].append(w)
    log.info("Distinct (user_id, subject) pairs: %d", len(by_pair))

    cycles_created = 0
    works_attached = 0

    for (user_id, subject), group in by_pair.items():
        # Если у пользователя+предмета уже есть цикл — переиспользуем
        existing = (
            db.query(ExamCycle)
            .filter(ExamCycle.user_id == user_id, ExamCycle.subject == subject)
            .order_by(ExamCycle.started_at.asc(), ExamCycle.id.asc())
            .first()
        )
        if existing is None:
            first_at = min((w.created_at for w in group if w.created_at), default=None)
            started_at = first_at.date() if first_at else None
            if started_at is None:
                log.warning("No created_at for user=%s subject=%s — skipping", user_id, subject)
                continue
            cycle = ExamCycle(
                user_id=user_id,
                subject=subject,
                ticket_id=None,
                started_at=started_at,
            )
            db.add(cycle)
            db.flush()
            cycles_created += 1
        else:
            cycle = existing

        # Per work_type считаем attempt_number в хронологическом порядке
        counters = {WORK_TYPE_MOCK_EXAM: 0, WORK_TYPE_RETAKE: 0}
        for w in sorted(group, key=lambda x: (x.work_type, x.created_at or 0)):
            counters[w.work_type] += 1
            w.cycle_id = cycle.id
            w.is_final = True
            w.attempt_number = counters[w.work_type]
            works_attached += 1

    if dry_run:
        log.info("[dry-run] would create %d cycles, attach %d works", cycles_created, works_attached)
        db.rollback()
    else:
        db.commit()
        log.info("Committed: %d cycles created, %d works attached", cycles_created, works_attached)

    return {"cycles_created": cycles_created, "works_attached": works_attached}


def main():
    dry = "--dry-run" in sys.argv
    db = SessionLocal()
    try:
        result = backfill(db, dry_run=dry)
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
