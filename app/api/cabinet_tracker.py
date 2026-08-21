"""«Личный трекер» ученика — плоский список: что горит, что в работе, что закрыто.

По макету созвона 17.08 это не календарь и не разбивка по дням — вертикальный
список задач с цветным статусом (`ITEM_KIND_LABELS`/`task_status`), как
Trello-чеклист без досок. Разбивка по дням недели и календарная полоска — это
`/cabinet/learning` («Актуальное образовательное пространство»), сюда она не
относится (см. `session-handoffs/current-program.md`, правка от 21.08).

Просроченное копится без нижней границы по времени — долг не имеет смысла
терять после смены недели, ученик должен видеть его, пока не закроет.
Выборка задач и их статус — общий движок `accessible_task_entries` из
`app/services/tracker.py`, тот же самый, что использует `/cabinet/learning`.

Раньше `/cabinet/tracker` был вторым именем общего дашборда ученика
(`cabinet_student.py`) — сюда переехала только его роль в навигации
(`STUDENT_NAV_ITEMS[key="tracker"]`), сам маршрут теперь самостоятельный.
Hero-карточка (имя/тариф/баллы) сюда не переезжает — её место в «Актуальном
образовательном пространстве», это отдельная задача.
"""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_csrf_header, require_student
from app.models.tracker import STATUS_DONE, STATUS_OPEN, TrackerTask, TrackerTaskState
from app.services.program import day_bounds, week_start
from app.services.tracker import accessible_task_entries, accessible_task_ids, task_status
from app.services.tz import today_msk, now_msk
from app.services.video_topics import accessible_topic_ids
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


@router.get("/tracker", response_class=HTMLResponse)
def cabinet_tracker(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    week_monday = week_start(today_msk())
    _, week_end = day_bounds(week_monday + timedelta(days=6))

    entries = accessible_task_entries(db, user["user_id"], start=None, end=week_end)

    overdue = [e for e in entries if e["status"] == "overdue"]
    upcoming = [e for e in entries if e["status"] == "upcoming"]
    # Сделанное показываем только за эту неделю — иначе список рос бы вечно
    # закрытыми делами месячной давности, которые уже никому не интересны.
    done = [e for e in entries if e["status"] == "done" and e["day"] >= week_monday]

    return templates.TemplateResponse("cabinet_tracker.html", {
        "request": request,
        "user": user,
        "overdue": overdue,
        "upcoming": upcoming,
        "done": done,
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
