"""«Личный трекер» ученика — неделя целиком, а не элементы по одному.

Показывает текущую московскую неделю: элементы программы (видео/пробник/
самостоятельная работа с их служебной темой `LearningTopic(kind='program_item')`)
и разовые задачи трекера, обе ветки — по своей адресации (`assign_to_all`,
теги, поимённо). Отметка выполнения — ленивая запись `TrackerTaskState`.

Раньше `/cabinet/tracker` был вторым именем общего дашборда ученика
(`cabinet_student.py`) — сюда переехала только его роль в навигации
(`STUDENT_NAV_ITEMS[key="tracker"]`), сам маршрут теперь самостоятельный.
Hero-карточка (имя/тариф/баллы) сюда не переезжает — её место в «Актуальном
образовательном пространстве» (`/cabinet/learning`), это отдельная задача.
"""
from datetime import timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_csrf_header, require_student
from app.models.tracker import (
    ITEM_KIND_LABELS,
    STATUS_DONE,
    STATUS_OPEN,
    TrackerTask,
    TrackerTaskState,
)
from app.services.program import WEEKDAY_LABELS, day_bounds, msk_date, week_start
from app.services.tracker import accessible_task_ids, task_status
from app.services.tz import MSK_TZ, today_msk, now_msk
from app.services.video_topics import accessible_topic_ids
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


def _week_task_query(db: DBSession, user_id: int, *, start, end):
    """Задачи текущей недели, доступные ученику — обе ветки адресации."""
    topic_ids = accessible_topic_ids(db, user_id)
    task_ids = accessible_task_ids(db, user_id)
    # .in_(set()) на пустом множестве — валидное «всегда ложь», без него
    # or_() с одним годным условием тоже отработает верно.
    return db.query(TrackerTask).filter(
        TrackerTask.is_published.is_(True),
        TrackerTask.deleted_at.is_(None),
        TrackerTask.due_at >= start,
        TrackerTask.due_at < end,
        or_(
            TrackerTask.topic_id.in_(topic_ids),
            TrackerTask.topic_id.is_(None) & TrackerTask.id.in_(task_ids),
        ),
    )


@router.get("/tracker", response_class=HTMLResponse)
def cabinet_tracker(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    monday = week_start(today_msk())
    days = [monday + timedelta(days=i) for i in range(7)]
    start, _ = day_bounds(monday)
    _, end = day_bounds(days[-1])

    tasks = _week_task_query(db, user["user_id"], start=start, end=end).order_by(
        TrackerTask.due_at.asc(), TrackerTask.sort_order.asc(), TrackerTask.id.asc()
    ).all()

    states = {}
    if tasks:
        rows = (
            db.query(TrackerTaskState)
            .filter(
                TrackerTaskState.task_id.in_([t.id for t in tasks]),
                TrackerTaskState.user_id == user["user_id"],
            )
            .all()
        )
        states = {row.task_id: row for row in rows}

    now = now_msk()
    items_by_day = {day: [] for day in days}
    for task in tasks:
        day = msk_date(task.due_at)
        if day not in items_by_day:
            continue
        due_at = task.due_at
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        items_by_day[day].append({
            "task": task,
            "kind_label": ITEM_KIND_LABELS.get(task.kind, task.kind),
            "status": task_status(task, states.get(task.id), now=now),
            "due_label": due_at.astimezone(MSK_TZ).strftime("%H:%M"),
        })

    today = today_msk()
    # "tasks", не "items": Jinja резолвит атрибуты раньше subscript'а, и
    # day.items утянул бы встроенный dict.items вместо списка задач.
    week_days = [
        {
            "date": day,
            "label": WEEKDAY_LABELS[day.weekday()],
            "tasks": items_by_day[day],
            "is_today": day == today,
            # Порядок фиксированный (не set()) — иначе точки в полоске недели
            # прыгали бы местами между перезагрузками страницы.
            "kinds": list(dict.fromkeys(entry["task"].kind for entry in items_by_day[day])),
        }
        for day in days
    ]

    return templates.TemplateResponse("cabinet_tracker.html", {
        "request": request,
        "user": user,
        "week_days": week_days,
        "week_start": monday,
        "week_end": days[-1],
        "active_tab": "tracker",
    })


@router.post("/tracker/tasks/{task_id}/toggle")
def cabinet_tracker_toggle(
    task_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task = db.get(TrackerTask, task_id)
    if task is None or task.deleted_at is not None or not task.is_published:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    topic_ids = accessible_topic_ids(db, user["user_id"])
    task_ids = accessible_task_ids(db, user["user_id"])
    accessible = (task.topic_id is not None and task.topic_id in topic_ids) or (
        task.topic_id is None and task.id in task_ids
    )
    if not accessible:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    state = (
        db.query(TrackerTaskState)
        .filter(
            TrackerTaskState.task_id == task_id,
            TrackerTaskState.user_id == user["user_id"],
        )
        .one_or_none()
    )
    if state is None:
        state = TrackerTaskState(task_id=task_id, user_id=user["user_id"], status=STATUS_OPEN)
        db.add(state)

    if state.status == STATUS_DONE:
        state.status = STATUS_OPEN
        state.completed_at = None
        state.completed_by_id = None
    else:
        state.status = STATUS_DONE
        state.completed_at = now_msk()
        state.completed_by_id = user["user_id"]

    db.commit()
    return JSONResponse({"status": task_status(task, state, now=now_msk())})
