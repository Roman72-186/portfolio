"""«Актуальное образовательное пространство» — стартовая вкладка ученика (трек A).

Скелет: рендерит первую незавершённую неделю по LearningTopic, с карточками-ссылками
на уже существующие «Видео» и «Обратная связь», кнопкой на созвон и заглушками
«Задание»/«Анкета» (реальные сущности — после решений Р1/Р2, см. AGENTS.md проекта
и plans/2026-08-18-apparchi-student-cabinet-and-guest-trial.md, раздел «Трек A»).

Понятия «неделя пройдена» в схеме пока нет (video_progress.py — прогресс по видео,
не по неделе целиком) — «актуальная» неделя здесь просто первая по sort_order/opens_at
среди доступных ученику тем. Полноценная адресация недель — фаза A2, после Р1/Р3.
"""
from typing import Annotated

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from app.api.cabinet_student import needs_profile_setup
from app.db.database import get_db
from app.dependencies import require_student
from app.models.learning_topic import LearningTopic
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

    return templates.TemplateResponse("cabinet_learning.html", {
        "request": request,
        "user": user,
        "topic": current_topic,
        "video_available": video_available,
    })
