"""Действия проверки по ученику: «просмотрено», отметки ответов и сдач, балл
точки А.

Проверка живёт во вкладке «Задания» карточки «Учеников» (05.10.2026,
владелец: «собрать всё, что касается ученика, в одно место»;
`cabinet_students_shared.py::get_student_tasks`, `cabinet_students.js::buildTasks`).
Недельная лента ученика снята, её адрес уводит в карточку: туда ведут
уведомления и закладки.

Экран-список «Проверка по ученику» — кого проверять, с поиском и фильтрами —
открывается из меню куратора, ГП и суперадмина. Строка ведёт во
вкладку «Задания» карточки. Куратор видит только назначенных ему учеников.

Сами действия остались на своих адресах: их зовут карточка, экран оценки
работы в задании (`task_block_feedback_detail.html`) и экран точки А.
"""

from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.constants import tariffs_for_data
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf_header, require_curator
from app.models.exam_cycle import ExamCycle
from app.models.task_block import TaskBlock, TaskBlockAnswer, TaskBlockResponse, TaskBlockSubmission
from app.models.user import User
from app.models.work import Work
from app.services.notify import notify
from app.services.point_a import maybe_notify_point_a_level
from app.services.review_aggregate import (
    FULL_ACCESS_RANK,
    NEWCOMER_TARIFF,
    NO_CURATOR,
    REVIEW_STATUS_CHECKED,
    REVIEW_STATUS_UNCHECKED,
    aggregate_student_review_counts,
    filter_review_rows,
    review_curator_options,
)
from app.services.task_blocks import is_control_submission, set_reviewed, set_submission_reviewed
from app.tmpl import templates

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


@router.get("", response_class=HTMLResponse)
@router.get("/queue", response_class=HTMLResponse)
def students_review_list(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    q: str = "",
    status: str = "",
    tariff: str = "",
    curator: str = "",
    subject: str = "",
    cycle_id: int | None = None,
):
    from app.services.curator_review_demo import can_handle_late_controls, can_preview_review, demo_review_url

    can_demo = can_preview_review(db, user)
    if request.url.path.endswith("/queue"):
        from app.models.role import Role
        from app.services.curator_review_queue import build_review_queue

        if not can_demo:
            return RedirectResponse("/cabinet/students", status_code=302)
        curators = db.query(User).join(Role, User.role_id == Role.id).filter(
            Role.rank == 2, User.is_active.is_(True), User.deleted_at.is_(None),
        ).order_by(User.name, User.id).all()
        selected = request.query_params.get("review_curator", str(user["user_id"]))
        selected_curator = next((item for item in curators if str(item.id) == selected), None)
        if selected_curator is None and curators:
            selected_curator = curators[0]
        curator_id = "all" if selected == "all" else selected_curator.id if selected_curator else user["user_id"]
        queue = build_review_queue(
            db, None if curator_id == "all" else curator_id,
            subject=subject, tariff=tariff, cycle_id=cycle_id, status=status,
            include_late=can_handle_late_controls(user),
        )
        for card in queue["cards"]:
            for work in card["works"]:
                if work["url"]:
                    work["url"] = demo_review_url(work["url"], curator_id)
        queue["subject_links"] = {
            key: demo_review_url(url, curator_id) for key, url in queue["subject_links"].items()
        }
        queue["queue_url"] = demo_review_url(queue["queue_url"], curator_id)
        return templates.TemplateResponse(request, "curator_review_queue.html", {
            "request": request, "user": user, "nav_active": "students_review",
            "demo_curators": curators, "demo_curator_id": curator_id, "review_demo": True, **queue,
        })
    native_rank = user.get("nav_rank") or user["role_rank"]
    can_filter_curator = native_rank >= FULL_ACCESS_RANK
    if not can_filter_curator:
        curator = ""
    all_rows = aggregate_student_review_counts(db, {**user, "role_rank": native_rank})
    rows = filter_review_rows(
        all_rows, q=q, status=status, tariff=tariff, curator=curator,
        search_contacts=True,
    )
    return templates.TemplateResponse(request, "staff_students_review.html", {
        "request": request,
        "user": user,
        "rows": [
            {**row, "href": student_tasks_url(row["student"].id)} for row in rows
        ],
        "total": len(all_rows),
        "can_demo_review": can_demo,
        "filters": {"q": q, "status": status, "tariff": tariff, "curator": curator},
        "is_filtered": bool(q.strip() or status or tariff or curator),
        # Тарифы — по ученикам этого списка (`tariffs_for_data`, инвариант
        # students.md): действующие всегда, старый — только если стоит у
        # кого-то из них.
        "tariffs": tariffs_for_data(row["student"].tariff for row in all_rows),
        "can_filter_curator": can_filter_curator,
        "curators": review_curator_options(db, all_rows) if can_filter_curator else [],
        "status_unchecked": REVIEW_STATUS_UNCHECKED,
        "status_checked": REVIEW_STATUS_CHECKED,
        "newcomer_tariff": NEWCOMER_TARIFF,
        "no_curator": NO_CURATOR,
        "nav_active": "students_review",
    })


@router.get("/holiday", response_class=HTMLResponse)
def holiday_review_list(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    flow: Annotated[str, Query(pattern=r"^\d{0,9}$")] = "",
    status: Literal["pending", "reviewed", "all"] = "pending",
    curator: Annotated[str, Query(pattern=r"^\d{0,9}$")] = "",
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
):
    from app.models.tracker import TrackerTask
    from app.services.holiday_review import (
        QUEUE_PATH, active_holiday_students, holiday_review_topic_ids, holiday_review_tracks,
        task_in_student_flow, tracks_for_topics,
    )
    from app.services.linked_feedback import pending_linked_control_main_ids, standalone_review_clause
    from app.api.cabinet_students_shared import _curator_options

    can_filter_curator = (user.get("nav_rank") or user["role_rank"]) >= FULL_ACCESS_RANK
    flow_id = int(flow) if flow else None
    curator_id = int(curator) if curator and can_filter_curator else None
    tracks = holiday_review_tracks(db).all()
    track_ids = {track.id for track in tracks}
    if flow_id is not None and flow_id not in track_ids:
        raise HTTPException(status_code=404, detail="Поток не найден")

    students = active_holiday_students(db, user)
    if curator_id is not None:
        students = students.filter(User.curator_id.is_(None) if curator_id == 0 else User.curator_id == curator_id)
    query = (
        db.query(TaskBlockSubmission, TrackerTask, User)
        .join(TaskBlock, TaskBlock.id == TaskBlockSubmission.block_id)
        .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
        .join(User, User.id == TaskBlockSubmission.user_id)
        .filter(
            User.id.in_(students.with_entities(User.id).statement),
            TaskBlockSubmission.submitted_at.is_not(None),
            TrackerTask.deleted_at.is_(None),
            TrackerTask.topic_id.in_(holiday_review_topic_ids()),
        )
    )
    if flow_id is not None:
        query = query.filter(task_in_student_flow(TrackerTask.topic_id, User.id, flow_id))
    pending_group_ids = pending_linked_control_main_ids(db, query)
    if status != "all":
        pending = TaskBlockSubmission.reviewed_at.is_(None) | TaskBlockSubmission.id.in_(pending_group_ids)
        query = query.filter(pending if status == "pending" else ~pending)
    query = query.filter(standalone_review_clause(db, query))
    page_size = 40
    rows = query.order_by(TaskBlockSubmission.submitted_at.desc(), TaskBlockSubmission.id.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size + 1).all()
    filters = {"status": status, **({"flow": flow_id} if flow_id is not None else {})}
    if curator_id is not None:
        filters["curator"] = curator_id
    tracks_by_topic = tracks_for_topics(
        db, {task.topic_id for _, task, _ in rows[:page_size]}, track_ids=track_ids,
    )
    return templates.TemplateResponse(request, "holiday_review_queue.html", {
        "tracks_by_topic": tracks_by_topic, "pending_group_ids": pending_group_ids,
        "user": user, "nav_active": "students_review", "rows": rows[:page_size],
        "tracks": tracks, "flow": flow_id, "curator": curator_id, "status": status,
        "can_filter_curator": can_filter_curator, "curators": _curator_options(db) if can_filter_curator else [],
        "work_query": urlencode(dict(filters, page=page, holiday_queue=1)),
        "prev_url": QUEUE_PATH + "?" + urlencode(dict(filters, page=page - 1)) if page > 1 else None,
        "next_url": QUEUE_PATH + "?" + urlencode(dict(filters, page=page + 1)) if len(rows) > page_size else None,
    })


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
    if is_control_submission(db, submission):
        raise HTTPException(status_code=409, detail="Статус контрольной обновляется после отправки ОС и сохранения балла.")
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
