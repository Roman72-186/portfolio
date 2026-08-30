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
from app.models.task_quiz import MAX_QUIZ_QUESTIONS
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
from app.services.task_quiz import (
    get_answers_map as get_task_quiz_answers_map,
    get_quiz_question_rows as get_task_quiz_question_rows,
    get_quiz_questions as get_task_quiz_questions,
    get_response as get_task_quiz_response,
    save_response as save_task_quiz_response,
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


# ── GET /cabinet/tracker/tasks/{id}/quiz ─────────────────────────────────────

@router.get("/tracker/tasks/{task_id}/quiz")
def cabinet_tracker_task_quiz(
    task_id: int,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    """Мини-опрос после сдачи любого элемента (владелец 30.08.2026, см.
    докстринг `app/models/task_quiz.py`) — тот же контракт JSON, что у
    видео и Пробника (`quiz_questions`/`quiz_answers`/`quiz_submit_endpoint`),
    но общий на material/quiz/lesson/checklist/homework/survey. Видео и
    Пробник сюда не ходят — у них свой embed-эндпоинт и свой гейт видимости
    (см. докстринг `app/models/task_quiz.py`)."""
    _accessible_task_or_404(db, user["user_id"], task_id)

    quiz_questions: list[str] = []
    quiz_answers: list[str] | None = None
    if _is_task_done(db, task_id, user["user_id"]):
        quiz_questions = get_task_quiz_questions(db, task_id)
        if quiz_questions:
            response = get_task_quiz_response(db, task_id=task_id, user_id=user["user_id"])
            if response is not None:
                question_rows = get_task_quiz_question_rows(db, task_id)
                answers_map = get_task_quiz_answers_map(db, response_id=response.id)
                quiz_answers = [answers_map.get(q.id, "") for q in question_rows]

    return JSONResponse({
        "quiz_questions": quiz_questions,
        "quiz_answers": quiz_answers,
        "quiz_submit_endpoint": f"/cabinet/tracker/tasks/{task_id}/quiz" if quiz_questions else None,
    })


class TrackerTaskQuizSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[str] = Field(min_length=1, max_length=MAX_QUIZ_QUESTIONS)

    @field_validator("answers")
    @classmethod
    def strip_answers(cls, value: list[str]) -> list[str]:
        return [item.strip()[:2000] for item in value]


# ── POST /cabinet/tracker/tasks/{id}/quiz ────────────────────────────────────

@router.post("/tracker/tasks/{task_id}/quiz", response_class=JSONResponse)
def submit_cabinet_tracker_task_quiz(
    task_id: int,
    payload: TrackerTaskQuizSubmit,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Сохранить ответы — только по факту уже закрытой задачи (сервер
    проверяет сам, клиенту не доверяет — та же дисциплина, что у
    `submit_mock_exam_quiz`)."""
    _accessible_task_or_404(db, user["user_id"], task_id)
    if not _is_task_done(db, task_id, user["user_id"]):
        raise HTTPException(status_code=409, detail="Сначала закройте задачу")
    question_rows = get_task_quiz_question_rows(db, task_id)
    if not question_rows:
        raise HTTPException(status_code=404, detail="Мини-опрос не настроен")
    if len(payload.answers) != len(question_rows):
        raise HTTPException(status_code=422, detail="Число ответов не совпадает с числом вопросов")
    save_task_quiz_response(
        db,
        task_id=task_id,
        user_id=user["user_id"],
        question_rows=question_rows,
        answers=payload.answers,
    )
    db.commit()
    return JSONResponse({"ok": True})
