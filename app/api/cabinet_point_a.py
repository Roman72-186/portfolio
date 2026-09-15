"""Экран «Оценка точки А» — вход Главного преподавателя (Лиза 14.09.2026).

Список учеников → карточка ученика с плашками по каждому элементу входной
оценки → балл 0–100 у каждой плашки → средний балл в шапке. Разобранные
ученики уезжают вниз списка и гаснут.

Почему отдельный роутер и экран, а не карточка внутри `/cabinet/staff/
students-review` — см. докстринг `app/services/point_a.py`. Здесь только
показ и одно действие: балл за портфолио «После». Всё остальное
переиспользуется:

- балл за работу (пробник, контрольная) — существующий
  `POST /cabinet/admin/works/{work_id}/score` (`cabinet_admin.py`), он же шлёт
  уведомление ученику и умеет вернуть на произвольный внутренний адрес;
- балл за портфолио «До» — существующий
  `POST /cabinet/staff/students-review/portfolio-before/{id}/score`
  (`student_review.py`), он остался на своём URL вместе со своими тестами.

Заводить здесь третью копию простановки балла нельзя: логика уведомления и
сброса счётчика непрочитанного живёт в тех эндпоинтах, и копия разошлась бы
с ними молча.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf_header
from app.models.user import User
from app.services.point_a import point_a_rows, student_point_a
from app.services.review_aggregate import _accessible_students
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/point-a")


def _student_or_404(db: DBSession, student_id: int) -> User:
    """Ранг 4 и выше видит всех активных учеников, поэтому проверки владения
    здесь нет — хватает того, что ученик существует и не в архиве (тот же
    приём, что в `student_review.py::score_portfolio_before`)."""
    student = (
        db.query(User)
        .filter(User.id == student_id, User.is_active == True)  # noqa: E712
        .first()
    )
    if student is None:
        raise HTTPException(status_code=404, detail="Ученик не найден")
    return student


@router.get("", response_class=HTMLResponse)
def point_a_list(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    rows = point_a_rows(db, _accessible_students(db, user))
    return templates.TemplateResponse(request, "staff_point_a.html", {
        "request": request,
        "user": user,
        "rows": rows,
        "nav_active": "point_a",
    })


@router.get("/{student_id}", response_class=HTMLResponse)
def point_a_detail(
    student_id: int,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    student = _student_or_404(db, student_id)
    return templates.TemplateResponse(request, "staff_point_a_detail.html", {
        "request": request,
        "user": user,
        "student": student,
        "point_a": student_point_a(db, student),
        "nav_active": "point_a",
    })


class PortfolioAfterScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int = Field(ge=0, le=100)


@router.post("/{student_id}/portfolio-after/score", response_class=JSONResponse)
def score_portfolio_after(
    student_id: int,
    payload: PortfolioAfterScore,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Одна оценка за весь набор работ «После» — пара к «До».

    Контракт дословно повторяет `score_portfolio_before`: тот же ранг, тот же
    диапазон, `extra="forbid"`, и `invalidate_session` так же не вызывается —
    балл не входит в user-dict сессии, а сбросить чужую сессию нечем.
    """
    student = _student_or_404(db, student_id)

    student.portfolio_after_score = payload.score
    student.portfolio_after_scored_at = datetime.now(timezone.utc)
    student.portfolio_after_scored_by_id = user["user_id"]
    db.commit()
    return JSONResponse({"ok": True, "score": payload.score})
