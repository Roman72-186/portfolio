"""«Актуальное образовательное пространство» — стартовая вкладка ученика (трек A).

Рендерит текущую неделю по LearningTopic как восемь вкладок в фиксированном
порядке (решение владельца 22.08/23.08, см.
plans/2026-08-22-apparchi-student-cabinet-open-questions.md, п.7/п.8.1):
Материалы → Видео → Тест по теории → Занятие → Задание → Чек-лист и проверки →
Анкета → Обратная связь. Следующая вкладка открыта, только когда закрыта
предыдущая — расчёт в `tracker.py::build_week_tabs`. Календарная полоска по
дням и отдельная страница дня (были здесь до 22.08) владельцем отменены —
ученик у Apparchi календаря по датам не видит вовсе.

Какую именно неделю показывать (заголовок/описание/ссылка на созвон), решает
`video_topics.py::current_week_topic` — там же записано, почему только
`kind=week` и почему самая поздняя из открытых. Понятия «неделя пройдена» в
схеме пока нет (video_progress.py — прогресс по видео, не по неделе целиком),
поэтому вернуть должника на незакрытую неделю система не умеет — это отдельный
нерешённый пункт Р1 в TODO.md, не путать с блокировкой вкладок внутри недели.

Список задач по-прежнему не привязан к `current_topic` жёстко: разовые задачи
без `topic_id` (адресованные тегом/«всем») тоже показываются здесь, если их
`due_at` попадает в текущую календарную неделю — тот же движок
`accessible_task_entries`, что и у «Личного трекера» (`app/api/cabinet_tracker.py`).
"""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_student
from app.services.program import day_bounds, item_details, week_start
from app.services.tracker import accessible_task_entries, build_week_tabs
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
    start, _ = day_bounds(monday)
    _, end = day_bounds(monday + timedelta(days=6))
    entries = accessible_task_entries(db, user["user_id"], start=start, end=end)

    return templates.TemplateResponse("cabinet_learning.html", {
        "request": request,
        "user": user,
        "topic": current_topic,
        "tabs": build_week_tabs(entries),
        # Нужен partial'у `partials/task_action.html`: без него видео недели
        # получило бы кнопку «Отметить» вместо ссылки на плеер.
        "details": item_details(db, [e["task"] for e in entries]),
    })
