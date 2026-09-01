"""Агрегатор «проверить всё по ученику за один заход» (созвон 01.09.2026).

Один адаптер на домен, общий формат на выходе — план
`plans/2026-09-01-apparchi-student-centric-review.md`, раздел «Архитектура».
Без новой таблицы: «непроверено» уже есть как предикат в каждом домене
(`Work.score IS NULL`, `TaskBlockAnswer.reviewed_at IS NULL`, ...), считать на
лету дешевле, чем городить индекс-таблицу, которая разойдётся с источником
при прямой правке в обход сервиса.

Этап 2 (текущий): скелет DTO и первый адаптер — обёртка над уже готовой
`task_blocks.py::review_queue`. Остальные адаптеры (`Work`, `HomeworkSubmission`,
`ExamCycle`/`Feedback`) — этап 3, блокирован открытыми вопросами владельцу
(см. план).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session as DBSession

DOMAIN_TASK_BLOCK = "task_block"


@dataclass(frozen=True)
class ReviewItem:
    """Общий формат строки в едином списке проверки, один на все домены."""

    domain: str
    item_id: int
    student_id: int
    title: str
    subject: str | None
    submitted_at: datetime | None
    is_reviewed: bool
    review_url: str


def _task_block_items(
    db: DBSession,
    *,
    curator_id: int | None = None,
    student_id: int | None = None,
    subject: str | None = None,
    tariff: str | None = None,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> list[ReviewItem]:
    """Ответы на блоки заданий — обёртка над `task_blocks.py::review_queue`.

    Запрос, скоуп куратора и фильтры уже сделаны там, здесь только приведение
    к общему DTO. `only_unreviewed=False`: агрегатору нужны и проверенные
    строки — это он сам решает, что показать выше.
    """
    from app.services.task_blocks import review_queue

    raw = review_queue(
        db,
        curator_id=curator_id,
        only_unreviewed=False,
        subject=subject,
        student_id=student_id,
        tariff=tariff,
        week_start=week_start,
        week_end=week_end,
        limit=100_000,
    )
    return [
        ReviewItem(
            domain=DOMAIN_TASK_BLOCK,
            item_id=row["answer_id"],
            student_id=row["student_id"],
            title=row["task_title"],
            subject=row["subject"],
            submitted_at=row["answered_at"],
            is_reviewed=row["reviewed"],
            review_url=f"/cabinet/staff/review?student={row['student_id']}&only=all",
        )
        for row in raw
    ]
