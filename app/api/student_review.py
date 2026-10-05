"""Действия проверки по ученику: «просмотрено», отметки ответов и сдач, балл
точки А.

Экраны этого роутера — список учеников с непроверенным и недельная лента
ученика — сняты 05.10.2026 (владелец: «собрать всё, что касается ученика, в
одно место»). Проверка живёт во вкладке «Задания» карточки «Учеников»
(`cabinet_students_shared.py::get_student_tasks`, `cabinet_students.js::buildTasks`),
очередь «кого проверять» — счётчиком в списке «Учеников». Старые адреса
экранов уводят туда же: на них ведут уведомления и закладки.

Сами действия остались на своих адресах: их зовут карточка, экран оценки
работы в задании (`task_block_feedback_detail.html`) и экран точки А.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf_header, require_curator
from app.models.exam_cycle import ExamCycle
from app.models.task_block import TaskBlockAnswer, TaskBlockResponse, TaskBlockSubmission
from app.models.user import User
from app.models.work import Work
from app.services.notify import notify
from app.services.point_a import maybe_notify_point_a_level
from app.services.review_aggregate import FULL_ACCESS_RANK
from app.services.task_blocks import set_reviewed, set_submission_reviewed

router = APIRouter(prefix="/cabinet/staff/students-review")


def student_tasks_url(student_id: int) -> str:
    """Где теперь проверяют ученика — вкладка «Задания» его карточки."""
    return f"/cabinet/students?student={student_id}&tab=tasks"


def _check_student_access(
    db: DBSession, user: dict, student_id: int, *, not_found_detail: str, forbidden_detail: str,
) -> User:
    """rank < FULL_ACCESS_RANK (куратор и модератор) — только свои ученики.

    Проверка явно охватывает обе staff-роли ниже ГП: без неё модератор,
    которого пропускает `require_curator`, видел бы чужих учеников."""
    student = db.query(User).filter(User.id == student_id, User.is_active == True).first()  # noqa: E712
    if student is None:
        raise HTTPException(status_code=404, detail=not_found_detail)
    if user["role_rank"] < FULL_ACCESS_RANK and student.curator_id != user["user_id"]:
        raise HTTPException(status_code=403, detail=forbidden_detail)
    return student


@router.get("")
def students_review_list(user: Annotated[dict, Depends(require_curator)]):
    return RedirectResponse("/cabinet/students", status_code=302)


@router.get("/{student_id}")
def student_review_detail(student_id: int, user: Annotated[dict, Depends(require_curator)]):
    # Своего ли ученика открывают, решает карточка (`_check_access`) — здесь
    # только переадресация, без чтения базы.
    return RedirectResponse(student_tasks_url(student_id), status_code=302)


# ── Действия прямо с экрана (этап 6) ────────────────────────────────────────
#
# «Просмотрено» только отмечает и не снимает отметку — в отличие от
# TaskBlockAnswer (там снятие нужно: ткнули случайно на входящей очереди),
# здесь строка с балл/закрытием уже выходит из непроверенных сама, отдельная
# кнопка «просмотрено» нужна только чтобы добавить признак, не убрать чужой.


@router.post("/work/{work_id}/viewed", response_class=JSONResponse)
def mark_work_viewed(
    work_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    if work.needs_revision:
        raise HTTPException(status_code=409, detail="Сначала дождитесь новой сдачи")
    _check_student_access(
        db, user, work.user_id,
        not_found_detail="Работа не найдена",
        forbidden_detail="Это не ваш студент",
    )
    work.viewed_at = work.viewed_at or datetime.now(timezone.utc)
    work.viewed_by_id = user["user_id"]
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/cycle/{cycle_id}/viewed", response_class=JSONResponse)
def mark_cycle_viewed(
    cycle_id: int,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    cycle = db.get(ExamCycle, cycle_id)
    if cycle is None:
        raise HTTPException(status_code=404, detail="Цикл не найден")
    _check_student_access(
        db, user, cycle.user_id,
        not_found_detail="Цикл не найден",
        forbidden_detail="Это не ваш студент",
    )
    cycle.viewed_at = cycle.viewed_at or datetime.now(timezone.utc)
    cycle.viewed_by_id = user["user_id"]
    db.commit()
    return JSONResponse({"ok": True})


class TaskBlockReviewMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed: bool = True
    comment: str | None = Field(default=None, max_length=4000)


@router.post("/task-block/{answer_id}/reviewed", response_class=JSONResponse)
def mark_task_block_reviewed(
    answer_id: int,
    payload: TaskBlockReviewMark,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Тумблер «Просмотрено» для ответа на блок задания (снос отдельного
    экрана `/cabinet/staff/review` 02.09.2026, там кнопка была такая же).

    Владелец ученика ответа берём из `TaskBlockResponse.user_id`, а не из
    тела запроса — иначе куратор мог бы подставить своего ученика и снять/
    поставить отметку на чужом ответе (тот же класс дыры, что чинили в
    `_check_student_access` 01.09.2026)."""
    row = (
        db.query(TaskBlockAnswer, TaskBlockResponse)
        .join(TaskBlockResponse, TaskBlockResponse.id == TaskBlockAnswer.response_id)
        .filter(TaskBlockAnswer.id == answer_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ответ не найден")
    _answer, response = row
    _check_student_access(
        db, user, response.user_id,
        not_found_detail="Ответ не найден",
        forbidden_detail="Это не ваш студент",
    )
    answer = set_reviewed(
        db, answer_id=answer_id, user_id=user["user_id"], reviewed=payload.reviewed
    )
    db.commit()
    return JSONResponse({"ok": True, "reviewed": answer.reviewed_at is not None})


@router.post("/block-work/{submission_id}/reviewed", response_class=JSONResponse)
def mark_block_work_reviewed(
    submission_id: int,
    payload: TaskBlockReviewMark,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Тумблер «Проверено» для работы, сданной в блоке задания.

    Владелец работы берётся из самой сдачи, а не из тела запроса — та же
    защита, что у `mark_task_block_reviewed`.
    """
    submission = db.get(TaskBlockSubmission, submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Работа не найдена")
    if submission.needs_revision:
        raise HTTPException(status_code=409, detail="Сначала дождитесь новой сдачи")
    _check_student_access(
        db, user, submission.user_id,
        not_found_detail="Работа не найдена",
        forbidden_detail="Это не ваш студент",
    )
    submission = set_submission_reviewed(
        db, submission_id=submission_id, user_id=user["user_id"],
        reviewed=payload.reviewed,
        comment=payload.comment.strip() if payload.comment is not None else None,
    )
    db.commit()
    return JSONResponse({
        "ok": True,
        "reviewed": submission.reviewed_at is not None,
        "comment": submission.review_comment,
    })


class PortfolioBeforeScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int = Field(ge=0, le=100)


@router.post("/portfolio-before/{student_id}/score", response_class=JSONResponse)
def score_portfolio_before(
    student_id: int,
    payload: PortfolioBeforeScore,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
    background_tasks: BackgroundTasks,
):
    """Точка А — одна оценка за весь набор работ «До».

    `require_admin_role` (ранг 4), а не `require_curator` с ручной проверкой:
    владелец 09.09.2026 на вопрос «кто ставит оценку» ответил «только Главный
    преподаватель». У ранга 4 и выше доступ ко всем ученикам не ограничен
    куратором, поэтому отдельная проверка владения здесь не нужна — хватает
    того, что ученик существует и активен.

    `invalidate_session` не вызывается намеренно: балл не входит в user-dict
    сессии (см. комментарий у колонок в `app/models/user.py`), а сбросить
    чужую сессию из Redis всё равно нечем.
    """
    student = (
        db.query(User)
        .filter(User.id == student_id, User.is_active == True)  # noqa: E712
        .first()
    )
    if student is None:
        raise HTTPException(status_code=404, detail="Ученик не найден")

    student.portfolio_before_score = payload.score
    student.portfolio_before_scored_at = datetime.now(timezone.utc)
    student.portfolio_before_scored_by_id = user["user_id"]
    notification = maybe_notify_point_a_level(db, student)
    db.commit()
    if notification is not None:
        invalidate_unread(student.id)
        background_tasks.add_task(notify, notification.id)
    return JSONResponse({"ok": True, "score": payload.score})
