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
from app.constants import TARIFF_DISPLAY, TARIFFS_CURRENT
from app.dependencies import require_csrf, require_payments_staff
from app.models.payment import COHORT_FROM
from app.services import payment_import as pi
from app.services import payments
from app.services.user_management import _invalidate_user_sessions
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/superadmin")

# Кому открыто — ГП и суперадмин (`dependencies.require_payments_staff`).


async def _read_rows(file: UploadFile | None, usernames: str, defaults: pi.Defaults) -> list[pi.SheetRow]:
    try:
        if usernames.strip():
            if file:
                raise pi.ImportFileError("Выберите файл или вставьте список ников")
            return pi.parse_usernames(usernames, defaults)
        if not file:
            raise pi.ImportFileError("Выберите файл или вставьте список ников")
        return pi.parse_workbook(await file.read(pi.MAX_FILE_BYTES + 1))
    except pi.ImportFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _defaults(start: str, end: str, month: str, tariff: str = "") -> pi.Defaults:
    try:
        return pi.parse_defaults(start, end, month, tariff)
    except payments.PaymentSettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/payment-import", response_class=HTMLResponse)
def payment_import_page(
    request: Request,
    user: Annotated[dict, Depends(require_payments_staff)],
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
        "tariff_options": [
            (tariff, TARIFF_DISPLAY.get(tariff, tariff),
             payments.rub_text(amount) if (amount := payments.reference_price_kop(db, tariff, COHORT_FROM)) else "")
            for tariff in TARIFFS_CURRENT
        ],
    })


@router.post("/payment-import/preview")
async def payment_import_preview(
    user: Annotated[dict, Depends(require_payments_staff)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    file: UploadFile | None = File(None),
    usernames: str = Form(""),
    window_start: str = Form(""),
    window_end: str = Form(""),
    paid_month: str = Form(""),
    tariff: str = Form(""),
):
    """Что произойдёт с каждой строкой файла. В базу не пишет.

    Окно и оплаченный месяц с формы — общие на загрузку, ложатся только в
    пустые поля ученика."""
    defaults = _defaults(window_start, window_end, paid_month, tariff if usernames.strip() else "")
    rows = await _read_rows(file, usernames, defaults)
    results = pi.build_preview(db, rows, defaults)
    return JSONResponse({"ok": True, "rows": pi.preview_json(results), "counts": pi.counts(results)})


@router.post("/payment-import/apply")
async def payment_import_apply(
    user: Annotated[dict, Depends(require_payments_staff)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf)],
    file: UploadFile | None = File(None),
    usernames: str = Form(""),
    choices: str = Form(""),
    window_start: str = Form(""),
    window_end: str = Form(""),
    paid_month: str = Form(""),
    tariff: str = Form(""),
):
    """Записать отмеченные строки. `choices` — JSON `[{"line": 5, "user_id": 12}]`.

    Файл приходит второй раз и разбирается заново: что записать, решает сервер
    по файлу, от браузера берём только выбор человека."""
    defaults = _defaults(window_start, window_end, paid_month, tariff if usernames.strip() else "")
    rows = await _read_rows(file, usernames, defaults)
    try:
        picked = {int(c["line"]): int(c["user_id"]) for c in json.loads(choices or "[]")}
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status_code=400, detail="Неверный список строк") from None
    if not picked:
        raise HTTPException(status_code=400, detail="Не отмечено ни одной строки")
    if len(picked) > pi.MAX_ROWS:
        raise HTTPException(status_code=400, detail="Слишком много отмеченных строк")

    summary = pi.apply_import(db, user["user_id"], rows, picked, defaults)
    db.commit()
    for student_id in summary.changed_ids:
        _invalidate_user_sessions(db, student_id)
    return JSONResponse({
        "ok": True,
        "written": summary.written,
        "unchanged": summary.unchanged,
        "skipped": summary.skipped,
    })
