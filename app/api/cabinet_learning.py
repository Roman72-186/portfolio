"""«Актуальное образовательное пространство» — стартовая вкладка ученика (трек A).

Скелет: рендерит текущую неделю по LearningTopic, с кнопкой на созвон и
заглушками «Задание»/«Анкета» (реальные сущности — после решений Р1/Р2, см.
AGENTS.md проекта и plans/2026-08-18-apparchi-student-cabinet-and-guest-trial.md,
раздел «Трек A»). Карточки «Видео» и «Обратная связь» убраны по просьбе
владельца 21.08 — доступ к ним не через эту вкладку.

Какую именно неделю показывать, решает `video_topics.py::current_week_topic` —
там же записано, почему только `kind=week` и почему самая поздняя из открытых.
Понятия «неделя пройдена» в схеме пока нет (video_progress.py — прогресс по
видео, не по неделе целиком), поэтому вернуть должника на незакрытую неделю
система не умеет. Полноценная адресация недель — фаза A2, после Р1/Р3.

**Разбивка по дням (21.08, правка после созвона 17.08).** Заказчик описал этот
экран как единственное место с календарной полоской: «сегодня/завтра/эта
неделя» — без навигации по месяцам, потому что ученик всегда видит ровно одну
активную неделю (следующая открывается только после этой). Разбивка — тот же
движок `accessible_task_entries`, что и у «Личного трекера»
(`app/api/cabinet_tracker.py`), никакой отдельной выборки здесь не заводим.
"""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_student
from app.models.tracker import ITEM_KIND_LABELS
from app.services.program import (
    WEEKDAY_LABELS,
    day_bounds,
    day_title_ru,
    item_details,
    parse_day_iso,
    week_start,
)
from app.services.tracker import accessible_task_entries
from app.services.tz import today_msk
from app.services.video_topics import current_week_topic
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


@router.get("/learning", response_class=HTMLResponse)
def cabinet_learning(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    current_topic = current_week_topic(db, user["user_id"])

    today = today_msk()
    monday = week_start(today)
    days = [monday + timedelta(days=i) for i in range(7)]
    start, _ = day_bounds(monday)
    _, end = day_bounds(days[-1])
    entries = accessible_task_entries(db, user["user_id"], start=start, end=end)

    entries_by_day = {day: [] for day in days}
    for entry in entries:
        if entry["day"] in entries_by_day:
            entries_by_day[entry["day"]].append(entry)

    # "tasks", не "items": Jinja резолвит атрибуты раньше subscript'а, и
    # day.items утянул бы встроенный dict.items вместо списка задач.
    week_days = [
        {
            "date": day,
            "label": WEEKDAY_LABELS[day.weekday()],
            "tasks": entries_by_day[day],
            "is_today": day == today,
            "kinds": list(dict.fromkeys(e["task"].kind for e in entries_by_day[day])),
        }
        for day in days
    ]

    return templates.TemplateResponse("cabinet_learning.html", {
        "request": request,
        "user": user,
        "topic": current_topic,
        "week_days": week_days,
        # Нужен partial'у `partials/task_action.html`: без него видео недели
        # получило бы кнопку «Отметить» вместо ссылки на плеер.
        "details": item_details(db, [e["task"] for e in entries]),
    })


@router.get("/learning/day/{iso}", response_class=HTMLResponse)
def cabinet_learning_day(
    iso: str,
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """День недели глазами ученика — read-only мирроринг `cabinet_program.program_day`.

    Тот же `accessible_task_entries`, что у обзора недели: ученик не может
    попасть на день, элементы которого ему не адресованы, просто увидит
    пустой день. Умные кнопки по `task.kind` — в шаблоне.
    """
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    day = parse_day_iso(iso)
    if day is None:
        raise HTTPException(status_code=404, detail="Такого дня нет")

    start, end = day_bounds(day)
    entries = accessible_task_entries(db, user["user_id"], start=start, end=end)
    details = item_details(db, [e["task"] for e in entries])

    return templates.TemplateResponse("cabinet_learning_day.html", {
        "request": request,
        "user": user,
        "day_iso": day.isoformat(),
        "day_title": day_title_ru(day),
        "back_href": f"/cabinet/learning#lrn-day-{day.isoformat()}",
        "entries": entries,
        "details": details,
        "kind_labels": ITEM_KIND_LABELS,
    })
