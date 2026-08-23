"""Заполнение анкеты учеником — вкладка «Анкета» в АОП (`cabinet_learning.html`).

Экран у вкладки не свой: у задачи типа `survey` есть `source_id` на живую
`Survey` (конструктор — `app/api/cabinet_program.py`), сервисный слой уже
собран в `app/services/survey.py`. Здесь — только маршруты: страница формы и
приём ответов, по образцу `homework_submission.py::student_homework_page` и
`video.py::submit_video_quiz` (тот же паттерн авто-закрытия трекер-задачи по
факту события, не по клиентской догадке).

Ответ ученика идемпотентен по `task_id` (докстрока `SurveyResponse`) —
повторная отправка формы того же появления анкеты перезаписывает прошлый
ответ, форма поэтому не запирается после первой отправки.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import require_csrf_header, require_student
from app.models.survey import Survey
from app.models.tracker import ITEM_SURVEY, SOURCE_SURVEY, TrackerTask
from app.services.survey import (
    get_answers,
    get_response,
    get_survey,
    serialize_for_student,
    submit_response,
)
from app.services.tracker import accessible_task_ids, accessible_topic_ids, close_task_for_user
from app.tmpl import templates

router = APIRouter(prefix="/cabinet")


class SurveyAnswerSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: int
    text: str | None = Field(default=None, max_length=2000)
    option_ids: list[int] = Field(default_factory=list, max_length=50)


class SurveySubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: list[SurveyAnswerSubmit] = Field(default_factory=list, max_length=200)


def _resolve_survey_task(db: DBSession, task_id: int) -> tuple[TrackerTask, Survey]:
    task = db.get(TrackerTask, task_id)
    if (
        task is None
        or task.deleted_at is not None
        or not task.is_published
        or task.kind != ITEM_SURVEY
        or task.source_kind != SOURCE_SURVEY
        or task.source_id is None
    ):
        raise HTTPException(status_code=404, detail="Анкета не найдена")
    survey = get_survey(db, task.source_id)
    if survey is None:
        raise HTTPException(status_code=404, detail="Анкета не найдена")
    return task, survey


def _guard_student_access(db: DBSession, task: TrackerTask, user_id: int) -> None:
    """Та же проверка, что у `homework_submission.py::_guard_student_access`."""
    accessible = (
        task.topic_id is not None and task.topic_id in accessible_topic_ids(db, user_id)
    ) or (task.topic_id is None and task.id in accessible_task_ids(db, user_id))
    if not accessible:
        raise HTTPException(status_code=404, detail="Анкета не найдена")


@router.get("/survey/{task_id}", response_class=HTMLResponse)
def student_survey_page(
    task_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
):
    task, survey = _resolve_survey_task(db, task_id)
    _guard_student_access(db, task, user["user_id"])

    payload = serialize_for_student(db, survey)
    response = get_response(db, task_id=task.id, user_id=user["user_id"])
    existing_answers = get_answers(db, response.id) if response is not None else {}

    return templates.TemplateResponse("cabinet_survey.html", {
        "request": request,
        "user": user,
        "task": task,
        "survey": payload,
        "existing_answers": existing_answers,
        "already_answered": response is not None,
        "submit_endpoint": f"/cabinet/survey/{task.id}/submit",
        "back_url": "/cabinet/learning",
    })


@router.post("/survey/{task_id}/submit", response_class=JSONResponse)
def submit_student_survey(
    task_id: int,
    payload: SurveySubmit,
    user: Annotated[dict, Depends(require_student)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task, survey = _resolve_survey_task(db, task_id)
    _guard_student_access(db, task, user["user_id"])

    try:
        submit_response(
            db,
            survey=survey,
            task_id=task.id,
            user_id=user["user_id"],
            answers=[answer.model_dump() for answer in payload.answers],
        )
    except ValueError:
        db.rollback()
        raise HTTPException(status_code=422, detail="Некорректный ответ на анкету")

    close_task_for_user(db, task, user["user_id"], source="auto")
    db.commit()
    return JSONResponse({"ok": True})
