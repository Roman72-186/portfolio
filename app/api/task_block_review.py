"""Очередь проверки ответов на блоки заданий.

Владелец 31.08.2026 после разбора чужих платформ: главный экран преподавателя —
не таблица, а **очередь**, «что накопилось на проверку». Так устроена «Лента
ответов» в GetCourse; Google Classroom и Moodle держат такой экран рядом с
таблицами. Ежедневная работа — разобрать новое, а не листать всех учеников.

Разница с GetCourse: там вручную проверяется всё, у нас вопросы с вариантами
проверяются сами. В очередь всё равно попадают все вопросы — владелец просил
видеть и тех, кто ошибся в вариантах, — но разбирать руками нужно только
свободные ответы.

Кто видит: куратор, главный преподаватель, суперадмин. **Куратору — только его
ученики** (`User.curator_id`, тот же приём, что в
`cabinet_students_shared.py::_get_accessible_students`). Ранг 3 попадает под то
же ограничение, что и куратор: владелец про модератора не говорил, и показать
меньше безопаснее, чем показать чужое.

Экраны «по ученику» и «по заданию» — следующие; данные у них те же.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS, TARIFFS
from app.db.database import get_db
from app.dependencies import require_csrf_header, require_curator
from app.models.user import User
from app.services.task_blocks import review_queue, set_reviewed
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/review")

# С этого ранга видно ответы всех учеников. Ниже — только своих.
FULL_ACCESS_RANK = 4


def _curator_scope(user: dict) -> int | None:
    """None — видно всех; иначе id, по которому фильтруются ученики."""
    return None if user.get("role_rank", 0) >= FULL_ACCESS_RANK else user["user_id"]


@router.get("", response_class=HTMLResponse)
def review_page(
    request: Request,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    only: str = "new",
    subject: str | None = None,
    student: int | None = None,
    tariff: str | None = None,
):
    scope = _curator_scope(user)
    items = review_queue(
        db,
        curator_id=scope,
        only_unreviewed=(only != "all"),
        subject=subject or None,
        student_id=student,
        tariff=tariff or None,
    )
    students_q = db.query(User.id, User.name).filter(User.is_active.is_(True))
    if scope is not None:
        students_q = students_q.filter(User.curator_id == scope)
    return templates.TemplateResponse(
        "staff_review_queue.html",
        {
            "request": request,
            "user": user,
            "items": items,
            "only": only,
            "subject": subject or "",
            "student": student or 0,
            "tariff": tariff or "",
            "subjects": MOCK_SUBJECTS,
            "tariffs": TARIFFS,
            "students": students_q.order_by(User.name).all(),
        },
    )


class ReviewMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed: bool = True


@router.post("/{answer_id}", response_class=JSONResponse)
def mark_reviewed(
    answer_id: int,
    payload: ReviewMark,
    user: Annotated[dict, Depends(require_curator)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Отметить ответ просмотренным или снять отметку.

    Снимать может тот же staff — ткнули случайно, надо уметь вернуть. Ученик
    отметку видит, но не трогает.

    Проверяем, что ответ вообще попадает в очередь этого пользователя: без
    этого куратор подставил бы в адрес чужой номер и отметил чужого ученика.
    """
    scope = _curator_scope(user)
    if scope is not None:
        allowed = {row["answer_id"] for row in review_queue(
            db, curator_id=scope, only_unreviewed=False, limit=100_000
        )}
        if answer_id not in allowed:
            raise HTTPException(status_code=404, detail="Ответ не найден")
    answer = set_reviewed(
        db, answer_id=answer_id, user_id=user["user_id"], reviewed=payload.reviewed
    )
    if answer is None:
        raise HTTPException(status_code=404, detail="Ответ не найден")
    db.commit()
    return JSONResponse({"ok": True, "reviewed": answer.reviewed_at is not None})
