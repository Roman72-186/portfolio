"""«Оплата списком»: загрузка Excel заказчика в настройки оплаты учеников.

Логика — `services/payment_import.py`, запись — `payments.apply_payment_settings`.
Страница живёт в разделе «Люди» (`section_access`, дерево
`/cabinet/superadmin/payment-import`), оба POST — под действием
`people:students`, как поля оплаты в карточке ученика.
"""
import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf
from app.services import payment_import as pi
from app.services.user_management import _invalidate_user_sessions
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/superadmin")


async def _read_rows(file: UploadFile) -> list[pi.SheetRow]:
    data = await file.read(pi.MAX_FILE_BYTES + 1)
    try:
        return pi.parse_workbook(data)
    except pi.ImportFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/payment-import", response_class=HTMLResponse)
def payment_import_page(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    students = [
        {"id": s.id, "name": pi.display_name(s)}
        for s in pi.load_students(db)
        if pi.is_writable(s)
    ]
    return templates.TemplateResponse(request, "superadmin_payment_import.html", {
        "request": request,
        "user": user,
        "students": students,
    })


@router.post("/payment-import/preview")
async def payment_import_preview(
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    file: UploadFile = File(...),
):
    """Что произойдёт с каждой строкой файла. В базу не пишет."""
    rows = await _read_rows(file)
    results = pi.build_preview(db, rows)
    return JSONResponse({"ok": True, "rows": pi.preview_json(results), "counts": pi.counts(results)})


@router.post("/payment-import/apply")
async def payment_import_apply(
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    file: UploadFile = File(...),
    choices: str = Form(""),
):
    """Записать отмеченные строки. `choices` — JSON `[{"line": 5, "user_id": 12}]`.

    Файл приходит второй раз и разбирается заново: что записать, решает сервер
    по файлу, от браузера берём только выбор человека."""
    rows = await _read_rows(file)
    try:
        picked = {int(c["line"]): int(c["user_id"]) for c in json.loads(choices or "[]")}
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status_code=400, detail="Неверный список строк") from None
    if not picked:
        raise HTTPException(status_code=400, detail="Не отмечено ни одной строки")

    summary = pi.apply_import(db, user["user_id"], rows, picked)
    db.commit()
    for student_id in summary.changed_ids:
        _invalidate_user_sessions(db, student_id)
    return JSONResponse({
        "ok": True,
        "written": summary.written,
        "unchanged": summary.unchanged,
        "skipped": summary.skipped,
    })
