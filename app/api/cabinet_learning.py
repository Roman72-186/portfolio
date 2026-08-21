"""«Актуальное образовательное пространство» — стартовая вкладка ученика (трек A).

Скелет: рендерит первую незавершённую неделю по LearningTopic, с карточками-ссылками
на уже существующие «Видео» и «Обратная связь», кнопкой на созвон и заглушками
«Задание»/«Анкета» (реальные сущности — после решений Р1/Р2, см. AGENTS.md проекта
и plans/2026-08-18-apparchi-student-cabinet-and-guest-trial.md, раздел «Трек A»).

Понятия «неделя пройдена» в схеме пока нет (video_progress.py — прогресс по видео,
не по неделе целиком) — «актуальная» неделя здесь просто первая по sort_order/opens_at
среди доступных ученику тем. Полноценная адресация недель — фаза A2, после Р1/Р3.

**Разбивка по дням (21.08, правка после созвона 17.08).** Заказчик описал этот
экран как единственное место с календарной полоской: «сегодня/завтра/эта
неделя» — без навигации по месяцам, потому что ученик всегда видит ровно одну
активную неделю (следующая открывается только после этой). Разбивка — тот же
движок `accessible_task_entries`, что и у «Личного трекера»
(`app/api/cabinet_tracker.py`), никакой отдельной выборки здесь не заводим.
"""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_student
from app.models.learning_topic import LearningTopic
from app.services.program import WEEKDAY_LABELS, day_bounds, week_start
from app.services.tracker import accessible_task_entries
from app.services.tz import today_msk
from app.services.video_catalog import list_published_videos
from app.services.video_topics import accessible_topic_ids
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

    topic_ids = accessible_topic_ids(db, user["user_id"])
    current_topic = None
    if topic_ids:
        current_topic = (
            db.query(LearningTopic)
            .filter(LearningTopic.id.in_(topic_ids))
            .order_by(LearningTopic.sort_order.asc(), LearningTopic.opens_at.asc())
            .first()
        )

    video_available = bool(list_published_videos(db, viewer=user))

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
        "video_available": video_available,
        "week_days": week_days,
    })
