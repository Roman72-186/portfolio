"""«Актуальное образовательное пространство» — стартовая вкладка ученика.

С 06.09.2026 экран рисует **единую ленту заданий цикла**, а не восемь вкладок
недели. Решение владельца (голосовое 06.09 01:37): «мне смысл эти восемь
кнопок держать? Просто открываем, устанавливаем, с какого по какое это будет
цикл, расставляем блоки друг за другом по порядку… после сохранения в таком же
виде появляется у ученика, уже с учётом доступности».

Что изменилось по сравнению с вкладками:

- порядок задаёт преподаватель (`due_at` + `sort_order`), а не фиксированная
  восьмёрка `WEEK_TAB_SEQUENCE`;
- шаг ленты — блок конструктора, и обязательный незакрытый блок запирает всё
  ниже, включая блоки следующих заданий;
- окно — период цикла (`LearningTopic.ends_at`), а не понедельник плюс шесть
  дней.

Сборка — `services/cycle_feed.py::feed_for_student`. Цикла может не быть вовсе
(экран его создания появится этапом 3): тогда окно падает на календарную
неделю по прежнему правилу, и ученик всё равно видит свои задачи — то же
решение, по которому 25.08.2026 убрали баннер «Пока нет ни одной доступной
недели».

Вкладка «Обратная связь» с этого экрана убрана: это не элемент программы, а
канал диалога, и он живёт отдельным пунктом меню (`/cabinet/cycle`).
"""
from typing import Annotated

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_student
from app.services.cycle_feed import feed_for_student
from app.services.program import item_details
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

    feed = feed_for_student(
        db,
        user_id=user["user_id"],
        user_tariff=user.get("tariff"),
        today=today_msk(),
    )

    return templates.TemplateResponse("cabinet_learning.html", {
        "request": request,
        "user": user,
        "feed": feed,
        # Нужен partial'у `partials/task_action.html` у шагов без блоков: без
        # него видео получило бы кнопку «Отметить» вместо ссылки на плеер.
        "details": item_details(db, [step["task"] for step in feed["steps"]]),
    })
