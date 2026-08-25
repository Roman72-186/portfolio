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
`tracker.py::effective_week_start` — первая незакрытая неделя ученика, а не
календарная (решение владельца 23.08, гейт «блок → неделя → месяц», Р1).
Должник видит свою застрявшую неделю, а не текущую; тема к ней резолвится
`tracker.py::week_topic_for_monday`.

Список задач по-прежнему не привязан к `current_topic` жёстко: разовые задачи
без `topic_id` (адресованные тегом/«всем») тоже показываются здесь, если их
`due_at` попадает в показанную (не обязательно календарную) неделю — тот же
движок `accessible_task_entries`, что и у «Личного трекера»
(`app/api/cabinet_tracker.py`). Поэтому у экрана нет отдельного «пустого»
состояния на случай отсутствия `LearningTopic`: заголовок/описание вкладки
падают на дефолтный текст, а восемь вкладок и их задачи всё равно строятся
по неделе (решение владельца 25.08.2026 — баннер «Пока нет ни одной доступной
недели» вводил в заблуждение, когда задачи у ученика фактически есть, а
`LearningTopic` на эту неделю просто не заведён).
"""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_student
from app.models.tracker import TAB_KIND_FEEDBACK
from app.services import feedback as fb_service
from app.services.program import day_bounds, item_details
from app.services.tracker import (
    accessible_task_entries,
    build_week_tabs,
    effective_week_start,
    week_topic_for_monday,
)
from app.services.tz import today_msk
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

    today = today_msk()
    monday = effective_week_start(db, user["user_id"], today)
    current_topic = week_topic_for_monday(db, user["user_id"], monday)

    start, _ = day_bounds(monday)
    _, end = day_bounds(monday + timedelta(days=6))
    entries = accessible_task_entries(db, user["user_id"], start=start, end=end)

    # Вкладка «Обратная связь» переиспользует код цикла Пробника (решение
    # владельца 22.08) — тот же список открытых циклов, что и на полном экране
    # /cabinet/cycle, только без закрытых: закрытый цикл не требует действия
    # прямо сейчас, ему хватает полного экрана.
    open_cycles, _ = fb_service.list_student_cycle_cards(db, user["user_id"])

    tabs = build_week_tabs(entries)
    for tab in tabs:
        # `build_week_tabs` не знает про ExamCycle — маркер «Обратной связи»
        # считаем здесь, тем же признаком, что красит карточку «Новое ·
        # N» внутри вкладки (решение владельца 25.08.2026).
        if tab["kind"] == TAB_KIND_FEEDBACK:
            tab["has_unread"] = any((c.get("unread_count") or 0) > 0 for c in open_cycles)

    return templates.TemplateResponse("cabinet_learning.html", {
        "request": request,
        "user": user,
        "topic": current_topic,
        "tabs": tabs,
        "open_cycles": open_cycles,
        # Нужен partial'у `partials/task_action.html`: без него видео недели
        # получило бы кнопку «Отметить» вместо ссылки на плеер.
        "details": item_details(db, [e["task"] for e in entries]),
    })
