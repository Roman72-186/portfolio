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

Hero-карточка (аватар/имя/тариф/баллы Р-К/год поступления) — решение владельца
21.08: живёт здесь, не в АОП. Partial `partials/profile_hero.html`, стили —
`app/static/css/profile_hero.css`, данные по баллам — общий сервис
`app/services/stats.py::avg_score_by_subject_all_time` (тот же, что у карточки
ученика для персонала).
"""
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_csrf_header, require_student
from app.models.task_block import (
    BLOCK_PHOTO, BLOCK_QUESTION, BLOCK_VIDEO, MAX_BLOCKS, QUESTION_TEXT,
)
from app.models.tracker import (
    EVENT_KIND_LABELS,
    ITEM_HOMEWORK,
    ITEM_MOCK_EXAM,
    STATUS_DONE,
    STATUS_OPEN,
    TrackerTask,
    TrackerTaskState,
)
from app.services.program import day_bounds, item_details, week_start
from app.services.stats import avg_score_by_subject_all_time
from app.services.task_blocks import (
    get_answers_map as get_task_block_answers_map,
    get_blocks as get_task_blocks,
    get_images as get_task_block_images,
    grade_response as grade_task_blocks,
    get_options as get_task_block_options,
    get_response as get_task_block_response,
    get_selected_options as get_task_block_selected_options,
    question_blocks as task_question_blocks,
    save_response as save_task_block_response,
)
from app.services.tracker import (
    accessible_task_entries,
    accessible_task_ids,
    active_digest_for_student,
    active_goal_for_student,
    effective_week_start,
    list_events,
    task_status,
)
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

    today = today_msk()
    week_monday = week_start(today)
    _, week_end = day_bounds(week_monday + timedelta(days=6))

    entries = accessible_task_entries(db, user["user_id"], start=None, end=week_end)

    # Дайджест месяца — первый блок на экране (решение владельца 22.08).
    digest = active_digest_for_student(db, user["user_id"], year=today.year, month=today.month)
    digest_events = list_events(db, digest.id) if digest is not None else []
    goal = active_goal_for_student(db, user["user_id"], today=today)

    overdue = [e for e in entries if e["status"] == "overdue"]
    upcoming = [e for e in entries if e["status"] == "upcoming"]
    # Сделанное показываем только за эту неделю — иначе список рос бы вечно
    # закрытыми делами месячной давности, которые уже никому не интересны.
    #
    # Отбор по дате закрытия, не по дате дедлайна: закрытый сегодня долг
    # прошлой недели должен остаться на экране. Раньше здесь стояло
    # `e["day"] >= week_monday`, и такая задача исчезала совсем — из
    # «Просрочено» её выводил статус, в «Сделано» не пускал старый дедлайн.
    # Ученик жал галочку, обновлял страницу и не находил ни подтверждения,
    # ни задачи. Закрытые без отметки времени (до появления колонки) —
    # показываем, потерять их хуже, чем показать лишнее.
    done = [
        e for e in entries
        if e["status"] == "done"
        and (e["completed_on"] is None or e["completed_on"] >= week_monday)
    ]

    return templates.TemplateResponse("cabinet_tracker.html", {
        "request": request,
        "user": user,
        "overdue": overdue,
        "upcoming": upcoming,
        "done": done,
        "digest": digest,
        "digest_events": digest_events,
        "event_kind_labels": EVENT_KIND_LABELS,
        "goal": goal,
        # Красное предупреждение (решение владельца 23.08, гейт «блок → неделя
        # → месяц»): ученик застрял на прошлой неделе, а не идёт по текущей.
        "is_behind_schedule": effective_week_start(db, user["user_id"], today) < week_monday,
        "active_tab": "tracker",
        "avg_score_by_subject": avg_score_by_subject_all_time(db, user["user_id"]),
        # Нужен partial'у `partials/task_action.html`: видео, пробник и домашка
        # ведут на свой экран, галочка остаётся только у остального.
        "details": item_details(db, [e["task"] for e in entries]),
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

    # Домашка и пробник закрываются только фактом сдачи (тариф без обратной
    # связи) или кнопкой куратора «Принять работу»/«Закрыть цикл» (тариф с
    # обратной связью, решение владельца 23.08) — ручная отметка была бы
    # обходом гейта в одно нажатие.
    if task.kind in (ITEM_HOMEWORK, ITEM_MOCK_EXAM):
        raise HTTPException(status_code=403, detail="Эта задача закрывается автоматически")

    topic_ids = accessible_topic_ids(db, user["user_id"])
    task_ids = accessible_task_ids(db, user["user_id"])
    accessible = (task.topic_id is not None and task.topic_id in topic_ids) or (
        task.topic_id is None and task.id in task_ids
    )
    if not accessible:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    # Гейт «нельзя закрыть, пока не отвечены вопросы» (владелец 31.08.2026).
    # Считаем только **видимые сейчас** вопросы: скрытые до сдачи в проверку
    # не входят, иначе выходил бы тупик — вопрос не виден, ответить нельзя,
    # задание не закрыть, и вся неделя встала бы за ним.
    pending = [
        block for block in task_question_blocks(get_task_blocks(db, task_id))
        if not block.hidden_until_done
    ]
    if pending and get_task_block_response(
        db, task_id=task_id, user_id=user["user_id"]
    ) is None:
        raise HTTPException(
            status_code=409, detail="Сначала ответьте на вопросы задания"
        )

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

    # Отметка одноразовая, назад её снять нельзя (решение владельца
    # 26.08.2026): ученик должен быть уверен перед нажатием, а не полагаться
    # на то, что галочку можно снять и поставить заново. Повторный вызов на
    # уже закрытой задаче — не ошибка, а no-op: фронтенд блокирует кнопку
    # после первой отметки, но сам эндпоинт не должен откатывать состояние,
    # даже если запрос всё же прилетит повторно.
    if state.status != STATUS_DONE:
        state.status = STATUS_DONE
        state.completed_at = now_msk()
        state.completed_by_id = user["user_id"]
        db.commit()

    return JSONResponse({"status": task_status(task, state, now=now_msk())})


def _accessible_task_or_404(db: DBSession, user_id: int, task_id: int) -> TrackerTask:
    task = db.get(TrackerTask, task_id)
    if task is None or task.deleted_at is not None or not task.is_published:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    topic_ids = accessible_topic_ids(db, user_id)
    task_ids = accessible_task_ids(db, user_id)
    accessible = (task.topic_id is not None and task.topic_id in topic_ids) or (
        task.topic_id is None and task.id in task_ids
    )
    if not accessible:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task


def _is_task_done(db: DBSession, task_id: int, user_id: int) -> bool:
    state = (
        db.query(TrackerTaskState)
        .filter(TrackerTaskState.task_id == task_id, TrackerTaskState.user_id == user_id)
        .one_or_none()
    )
    return state is not None and state.status == STATUS_DONE


# ── GET /cabinet/tracker/tasks/{id}/blocks ───────────────────────────────────

@router.get("/tracker/tasks/{task_id}/blocks")
def cabinet_tracker_task_blocks(
    task_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Содержимое элемента: блоки конструктора по порядку плюс уже
    сохранённые ответы ученика (владелец 31.08.2026, см. докстринг
    `app/models/task_block.py`).

    Заменил прежний `/quiz`. Отличие в поведении, о котором стоит помнить:
    мини-опрос показывался **после** закрытия задачи (он был рефлексией по
    факту сдачи), а блоки — это содержимое самой задачи, и ученику они видны
    сразу, иначе он не увидит ни видео, ни текста, ни фото, которые нужны,
    чтобы задачу выполнить.

    Ролик отдаётся идентификатором: плеер запрашивает подписанный embed через
    существующий `/cabinet/videos/{id}/embed`, ключи Bunny в браузер не
    попадают. Доступ к такому ролику считается по блокам
    (`video_catalog.is_video_accessible`).
    """
    _accessible_task_or_404(db, user["user_id"], task_id)

    task_done = _is_task_done(db, task_id, user["user_id"])
    # Скрытый вопрос появляется только после закрытия задания — и весь блок
    # целиком, а не одна форма ответа: до сдачи ученик о нём не знает.
    blocks = [
        b for b in get_task_blocks(db, task_id)
        if not (b.block_type == BLOCK_QUESTION and b.hidden_until_done and not task_done)
    ]
    questions = task_question_blocks(blocks)
    options = get_task_block_options(db, [b.id for b in questions])

    answers_map: dict[int, str] = {}
    selected: dict[int, set[int]] = {}
    response = get_task_block_response(db, task_id=task_id, user_id=user["user_id"])
    answered = response is not None
    if response is not None:
        answers_map = get_task_block_answers_map(db, response_id=response.id)
        selected = get_task_block_selected_options(db, response_id=response.id)
    images = get_task_block_images(db, [b.id for b in blocks])
    # Вердикт отдаём, только когда ученик уже ответил. Иначе `is_correct` в
    # теле ответа подсказал бы верный вариант до отправки.
    verdict = (
        grade_task_blocks(db, blocks=blocks, response_id=response.id)
        if answered else None
    )
    correct_by_block = (
        {r["block_id"]: r["is_correct"] for r in verdict["results"]} if verdict else {}
    )

    payload = []
    for block in blocks:
        item = {
            "id": block.id,
            "block_type": block.block_type,
            "title": block.title,
            "body": block.body,
        }
        if block.block_type == BLOCK_VIDEO:
            item["video_id"] = block.video_id
            item["video_embed_endpoint"] = (
                f"/cabinet/videos/{block.video_id}/embed" if block.video_id else None
            )
        elif block.block_type == BLOCK_PHOTO:
            item["images"] = [
                {"url": i.image_s3_url} for i in images.get(block.id, [])
            ]
        elif block.block_type == "link":
            item["url"] = block.url
        elif block.block_type == BLOCK_QUESTION:
            item["question_type"] = block.question_type
            item["options"] = [
                # `is_correct` наружу не отдаём: ученик не должен видеть
                # правильный ответ в теле ответа сервера.
                {"id": o.id, "text": o.text}
                for o in options.get(block.id, [])
            ]
            item["answer_text"] = answers_map.get(block.id, "")
            item["answer_option_ids"] = sorted(selected.get(block.id, set()))
            item["is_correct"] = correct_by_block.get(block.id)
        payload.append(item)

    return JSONResponse({
        "blocks": payload,
        "has_questions": bool(questions),
        # Одна попытка (владелец 31.08.2026): ответил — форма закрывается.
        # Иначе, увидев «неверно», можно было бы переотправить до победы, и
        # счёт перестал бы что-либо значить.
        "answered": answered,
        "submit_endpoint": (
            f"/cabinet/tracker/tasks/{task_id}/blocks"
            if questions and not answered else None
        ),
        "correct_count": verdict["correct_count"] if verdict else None,
        "gradable_count": verdict["gradable_count"] if verdict else None,
    })


class TrackerBlockAnswerItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: int = Field(ge=1)
    text: str | None = Field(default=None, max_length=2000)
    option_ids: list[int] = Field(default_factory=list, max_length=20)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class TrackerTaskBlocksSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[TrackerBlockAnswerItem] = Field(min_length=1, max_length=MAX_BLOCKS)


# ── POST /cabinet/tracker/tasks/{id}/blocks ──────────────────────────────────

@router.post("/tracker/tasks/{task_id}/blocks", response_class=JSONResponse)
def submit_cabinet_tracker_task_blocks(
    task_id: int,
    payload: TrackerTaskBlocksSubmit,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Сохранить ответы на блоки-вопросы.

    Проверка доступа к элементу обязательна: `block_id` из чужой задачи не
    должен пройти — сервер сверяет каждый ответ с блоками именно этой задачи,
    клиенту не доверяет (та же дисциплина, что была у мини-опроса).

    Ответы принимаются частями: ученик может ответить на один вопрос из трёх
    и вернуться позже, поэтому «число ответов равно числу вопросов» больше не
    требуется — сохраняется то, что прислали.
    """
    _accessible_task_or_404(db, user["user_id"], task_id)
    # Одна попытка: сервер проверяет сам, кнопку на экране мало.
    if get_task_block_response(db, task_id=task_id, user_id=user["user_id"]) is not None:
        raise HTTPException(status_code=409, detail="Вы уже отвечали на это задание")
    task_done = _is_task_done(db, task_id, user["user_id"])
    all_blocks = get_task_blocks(db, task_id)
    questions = [
        b for b in task_question_blocks(all_blocks)
        if task_done or not b.hidden_until_done
    ]
    if not questions:
        raise HTTPException(status_code=404, detail="У задачи нет вопросов")
    known = {block.id for block in questions}
    unknown = [a.block_id for a in payload.answers if a.block_id not in known]
    if unknown:
        raise HTTPException(status_code=422, detail="Ответ на чужой вопрос")
    response = save_task_block_response(
        db,
        task_id=task_id,
        user_id=user["user_id"],
        blocks=questions,
        answers={
            a.block_id: {"text": a.text, "option_ids": a.option_ids}
            for a in payload.answers
        },
    )
    db.flush()
    verdict = grade_task_blocks(db, blocks=questions, response_id=response.id)
    db.commit()
    # Результат сразу в ответе (владелец 31.08.2026): ученик видит, где прав,
    # не перезагружая страницу.
    return JSONResponse({"ok": True, **verdict})
