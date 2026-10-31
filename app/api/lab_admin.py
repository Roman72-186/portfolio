"""Figure access editor for the head teacher and superadmin."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.constants import TARIFFS
from app.db.database import get_db
from app.dependencies import get_current_user, require_csrf_header
from app.models.audit_log import AuditLog
from app.models.lab_access import LabCardAccess, LabCardLevel, LabCardStudent, LabCardTag, LabCardTariff
from app.models.tag import Tag
from app.models.user import User
from app.models.role import Role
from app.services.lab_access import audiences, visible_cards
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/3dlab")


def _editor(user: dict) -> dict:
    if (user.get("nav_rank", 0) < 4 or user.get("role_name") == "модератор"
            or user.get("section_levels", {}).get("lab3d") == "none"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return user


class LabAudiencePayload(BaseModel):
    card_ids: list[str] = Field(min_length=1, max_length=1000)
    everyone: bool = False
    tariffs: list[str] = Field(default_factory=list)
    levels: list[int] = Field(default_factory=list)
    tag_ids: list[int] = Field(default_factory=list)
    user_ids: list[int] = Field(default_factory=list)


@router.get("/figures", response_class=HTMLResponse)
def figures_page(
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    _editor(user)
    rules = audiences(db)
    cards = [
        {**card, "audience": {
            "everyone": card_id in rules and rules[card_id].is_everyone,
            "tariffs": sorted(rules[card_id].tariffs) if card_id in rules else [],
            "levels": sorted(rules[card_id].levels) if card_id in rules else [],
            "tag_ids": sorted(rules[card_id].tag_ids) if card_id in rules else [],
            "user_ids": sorted(rules[card_id].user_ids) if card_id in rules else [],
        }}
        for card_id, card in visible_cards().items()
    ]
    students = db.query(User.id, User.name).join(Role, Role.id == User.role_id).filter(
        Role.rank == 1, User.is_active.is_(True)
    ).order_by(User.name).all()
    return templates.TemplateResponse(request, "lab_figures.html", {
        "request": request, "user": user, "cards": cards,
        "tariffs": TARIFFS, "tags": db.query(Tag).filter(Tag.is_hidden.is_(False)).order_by(Tag.name).all(),
        "students": [{"id": row.id, "name": row.name} for row in students],
    })


@router.post("/figures")
def save_figures(
    payload: LabAudiencePayload,
    user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[None, Depends(require_csrf_header)],
):
    _editor(user)
    cards = visible_cards()
    ids = set(payload.card_ids)
    if len(ids) != len(payload.card_ids) or not ids <= cards.keys():
        raise HTTPException(status_code=422, detail="Неизвестная фигура")
    tariffs = set(payload.tariffs)
    levels = set(payload.levels)
    tags = set(payload.tag_ids)
    students = set(payload.user_ids)
    if payload.everyone and (tariffs or levels or tags or students):
        raise HTTPException(status_code=422, detail="Доступ всем ученикам нельзя сочетать с фильтрами")
    if not tariffs <= set(TARIFFS) or not levels <= {1, 2}:
        raise HTTPException(status_code=422, detail="Неверный тариф или уровень")
    if tags and {row.id for row in db.query(Tag.id).filter(Tag.id.in_(tags))} != tags:
        raise HTTPException(status_code=422, detail="Неизвестный трек")
    if students and {row.id for row in db.query(User.id).join(Role, Role.id == User.role_id).filter(
        User.id.in_(students), Role.rank == 1, User.is_active.is_(True)
    )} != students:
        raise HTTPException(status_code=422, detail="Неизвестный ученик")
    for card_id in ids:
        for model in (LabCardTariff, LabCardLevel, LabCardTag, LabCardStudent):
            db.query(model).filter(model.card_id == card_id).delete(synchronize_session=False)
        if not (payload.everyone or tariffs or levels or tags or students):
            db.query(LabCardAccess).filter(LabCardAccess.card_id == card_id).delete(synchronize_session=False)
        else:
            if db.get(LabCardAccess, card_id) is None:
                db.add(LabCardAccess(card_id=card_id))
                db.flush()
            db.add_all([LabCardTariff(card_id=card_id, tariff=v) for v in tariffs])
            db.add_all([LabCardLevel(card_id=card_id, level=v) for v in levels])
            db.add_all([LabCardTag(card_id=card_id, tag_id=v) for v in tags])
            db.add_all([LabCardStudent(card_id=card_id, user_id=v) for v in students])
    for card_id in ids:
        db.add(AuditLog(
            action="lab_card_access", performed_by_id=user["user_id"],
            details=f"card:{card_id};everyone:{int(payload.everyone)};tariffs:{','.join(sorted(tariffs))};levels:{','.join(map(str, sorted(levels)))};tags:{','.join(map(str, sorted(tags)))};users:{','.join(map(str, sorted(students)))}"[:1000],
        ))
    db.commit()
    return {"updated": len(ids)}
