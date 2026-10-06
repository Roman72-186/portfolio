"""«Актуальное образовательное пространство» — стартовая вкладка ученика.

С 06.09.2026 экран рисует **единую ленту заданий цикла**, а не восемь вкладок
недели. Решение владельца (голосовое 06.09 01:37): «мне смысл эти восемь
кнопок держать? Просто открываем, устанавливаем, с какого по какое это будет
цикл, расставляем блоки друг за другом по порядку… после сохранения в таком же
виде появляется у ученика, уже с учётом доступности».

Что изменилось по сравнению с вкладками:

- порядок задаёт преподаватель (`due_at` + `sort_order`), а не фиксированная
  восьмёрка видов заданий (снесена этим же решением);
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
from app.services.cycle_feed import archive_cycle_ids, archive_for_student, feed_for_student
from app.services.program import item_details
from app.services.task_blocks import completion_blocker, completion_button_needed
from app.services.tz import msk_text, today_msk
from app.constants import SUPPORT_URL
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


def _completion_state(db: DBSession, user: dict, feed: dict) -> tuple[dict, set]:
    """Почему «Завершить задание» пока нельзя нажать — то же правило, по
    которому откажет сама кнопка (`task_blocks.completion_blocker`). Только у
    незакрытых заданий с блоками: у задания без блоков вопросов нет.

    Задание, которое закроется само, кнопку не получает вовсе (владелец
    01.10.2026, `task_blocks.completion_button_needed`); «Задание выполнено»
    у закрытого остаётся."""
    completion_blockers = {}
    completion_hidden = set()
    for step in feed["steps"]:
        task_id = step["task"].id
        if (
            not step.get("block") or step["entry"]["status"] == "done"
            or task_id in completion_blockers or task_id in completion_hidden
        ):
            continue
        if not completion_button_needed(
            db, task_id=task_id, user_id=user["user_id"], user_tariff=user.get("tariff")
        ):
            completion_hidden.add(task_id)
            continue
        completion_blockers[task_id] = completion_blocker(
            db, task_id=task_id, user_id=user["user_id"], user_tariff=user.get("tariff")
        )
    return completion_blockers, completion_hidden


@router.get("/learning", response_class=HTMLResponse)
def cabinet_learning(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    cycle: int | None = None,
    task: int | None = None,
):
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)

    # `?cycle=` — возврат в пройденный цикл (владелец 03.09.2026: «он может
    # вернуться в этот цикл… если он задание выполнил, у него будет доступно к
    # пересмотру, к перечитыванию своих ответов, но без возможности изменить»).
    feed = feed_for_student(
        db,
        user_id=user["user_id"],
        user_tariff=user.get("tariff"),
        today=today_msk(),
        cycle_id=cycle,
    )
    # Ссылка `?task=` из трекера фокусирует только задание текущей ленты.
    # Долг другого цикла трекер ведёт сюда через `?cycle=<id>#learning-task-<id>`
    # (владелец 06.10.2026: «чтобы могли досдать», отменяет прежнее «старые
    # долги без кнопки перехода»); цикл по task_id здесь не подбираем.
    focus_task_id = None
    focus_subject = None
    if cycle is None and task is not None and any(
        pinned["id"] == task for pinned in feed["pinned_tasks"]
    ) and feed["stage"] is not None:
        # Задание этапа («Портфолио») живёт не в ленте цикла, а в ленте
        # самого этапа (владелец 29.09.2026) — туда трекер и ведёт.
        feed = feed_for_student(
            db,
            user_id=user["user_id"],
            user_tariff=user.get("tariff"),
            today=today_msk(),
            cycle_id=feed["stage"]["id"],
        )
    if cycle is None and task is not None:
        target = next(
            (step for step in feed["steps"] if step["task"].id == task), None
        )
        if target is not None:
            focus_task_id = task
            # Пустая строка — вкладка «Общее»: задание без предмета.
            focus_subject = target["subject"] or ""

    completion_blockers, completion_hidden = _completion_state(db, user, feed)

    return templates.TemplateResponse(request, "cabinet_learning.html", {
        "request": request,
        "user": user,
        "feed": feed,
        "focus_task_id": focus_task_id,
        "focus_subject": focus_subject,
        # Нужен partial'у `partials/task_action.html` у шагов без блоков: без
        # него видео получило бы кнопку «Отметить» вместо ссылки на плеер.
        "details": item_details(db, [step["task"] for step in feed["steps"]]),
        "completion_blockers": completion_blockers,
        "completion_hidden": completion_hidden,
        "onboarding_auto_open": request.query_params.get("welcome") == "1",
        "onboarding_on_learning": True,
        "onboarding_access_until_text": msk_text(user.get("access_until")),
        "support_url": SUPPORT_URL,
    })


@router.get("/learning/archive", response_class=HTMLResponse)
def cabinet_learning_archive(
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Архив: пройденные циклы, период → этап → цикл (владелец 05.10.2026).
    Сборка — `cycle_feed.archive_for_student`, цикл открывается тут же, в
    архиве (`/cabinet/learning/archive/{id}`), а не в ленте обучения."""
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)
    periods = archive_for_student(
        db, user_id=user["user_id"], user_tariff=user.get("tariff"), today=today_msk(),
    )
    return templates.TemplateResponse(request, "cabinet_learning_archive.html", {
        "request": request,
        "user": user,
        "periods": periods,
        "active_tab": "archive",
    })


@router.get("/learning/archive/{cycle_id}", response_class=HTMLResponse)
def cabinet_learning_archive_cycle(
    cycle_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Пройденный цикл целиком внутри архива (владелец 05.10.2026: «при
    открытии цикла не нужно перекидывать в АОП, а открывать здесь же весь
    цикл»). Та же лента, что `/cabinet/learning?cycle=`, — задания, свои
    работы, ответы преподавателя, — но без карусели циклов и плашки долга,
    под вкладкой «Архив» и без кнопки «Завершить задание».

    Открывается только цикл из списка архива (`archive_cycle_ids`): иначе
    подобранный в адресной строке номер открыл бы текущий цикл под видом
    архива или запертый долгом."""
    if needs_profile_setup(user):
        return RedirectResponse("/cabinet/profile", status_code=302)
    today = today_msk()
    if cycle_id not in archive_cycle_ids(
        db, user_id=user["user_id"], user_tariff=user.get("tariff"), today=today,
    ):
        return RedirectResponse("/cabinet/learning/archive", status_code=302)
    feed = feed_for_student(
        db, user_id=user["user_id"], user_tariff=user.get("tariff"), today=today,
        cycle_id=cycle_id,
    )
    # Архив — только просмотр, и у выполненного идущего цикла тоже: кнопку
    # «Завершить задание» шаблон прячет по этому флагу.
    feed["is_archive"] = True
    return templates.TemplateResponse(request, "cabinet_learning.html", {
        "request": request,
        "user": user,
        "feed": feed,
        "archive_view": True,
        "focus_task_id": None,
        "focus_subject": None,
        "details": item_details(db, [step["task"] for step in feed["steps"]]),
        "completion_blockers": {},
        "completion_hidden": set(),
        "onboarding_auto_open": False,
        "onboarding_on_learning": False,
        "onboarding_access_until_text": msk_text(user.get("access_until")),
        "support_url": SUPPORT_URL,
    })
