"""Campaign links and funnel reporting for the Telegram bot."""
import csv
import io
import hmac
import os
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.dependencies import get_current_user, require_csrf
from app.tmpl import templates

router = APIRouter()
PAGE = "/cabinet/superadmin/stats/telegram"
STATISTICS_HOME = "/cabinet/staff/statistics"
DB = Annotated[Session, Depends(get_db)]
CSRF = Annotated[None, Depends(require_csrf)]
UTM = ("utm_source", "utm_medium", "utm_campaign", "utm_content")
MSK = timezone(timedelta(hours=3))


def require_report_user(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if user["nav_rank"] < 3:
        raise HTTPException(403)
    return user


def can_edit_links(user: dict) -> bool:
    return user["role_name"] in ("админ", "суперадмин")


def require_link_editor(user: Annotated[dict, Depends(require_report_user)]) -> dict:
    if not can_edit_links(user):
        raise HTTPException(403)
    return user


ReportUser = Annotated[dict, Depends(require_report_user)]
Admin = Annotated[dict, Depends(require_link_editor)]


@router.get(STATISTICS_HOME)
def statistics_home(request: Request, user: ReportUser):
    return templates.TemplateResponse(request, "staff_statistics.html", {"user": user, "page": PAGE})


def record(db, kind, code=None, uid=None, value="", event_key=None, payment_ref=None, at=None):
    db.execute(text("""
        INSERT INTO bot_events (uid, code, kind, value, event_key, payment_ref, at)
        VALUES (:uid, :code, :kind, :value, :event_key, :payment_ref, :at)
        ON CONFLICT (event_key) DO NOTHING
    """), dict(uid=uid, code=code, kind=kind, value=value, event_key=event_key,
              payment_ref=payment_ref, at=at or datetime.now(timezone.utc)))
    db.commit()


def find_link(db, code):
    return db.execute(text("SELECT * FROM bot_links WHERE code=:code"), {"code": code}).mappings().first()


@router.get("/go/telegram/{code}")
def open_link(code: str, db: DB):
    username = os.getenv("FUNNEL_BOT_USERNAME", "")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) or not find_link(db, code):
        raise HTTPException(404)
    record(db, "click", code)
    return RedirectResponse(f"https://t.me/{username}?start={code}", headers={"Cache-Control": "no-store"})


class Event(BaseModel):
    uid: int = Field(gt=0, lt=2**63)
    kind: Literal["start", "button", "material", "step", "situation"]
    value: str = Field(default="", max_length=150)
    event_key: str = Field(pattern=r"^[a-f0-9]{32}$")
    at: AwareDatetime


@router.post("/internal/telegram-analytics")
def ingest(event: Event, db: DB, authorization: Annotated[str, Header()] = ""):
    key = os.getenv("FUNNEL_ANALYTICS_KEY", "")
    if not key or not secrets.compare_digest(authorization.encode(), f"Bearer {key}".encode()):
        raise HTTPException(401)
    link = find_link(db, event.value) if event.kind == "start" else None
    ref = hmac.digest(key.encode(), f"funnel-payment:{event.uid}".encode(), "sha256").hex()
    record(db, event.kind, link["code"] if link else None, event.uid, event.value,
           event.event_key, ref, event.at)
    return {"ok": True}


@router.get("/go/telegram-payment")
def open_payment(request: Request, db: DB):
    token = request.query_params.get("ref", "")
    uid = db.execute(text("SELECT uid FROM bot_events WHERE payment_ref=:ref LIMIT 1"),
                     {"ref": token}).scalar()
    if uid is not None:
        record(db, "payment_click", uid=uid)
    utm = db.execute(text("""
        SELECT l.* FROM bot_events e LEFT JOIN bot_links l ON l.code=e.code
        WHERE e.uid=:uid AND e.kind='start' ORDER BY e.id LIMIT 1
    """), {"uid": uid}).mappings().first()
    query = urlencode({name: utm[name] for name in UTM if utm and utm[name]})
    return RedirectResponse("/testpodgotovki" + ("?" + query if query else ""),
                            headers={"Cache-Control": "no-store"})


@router.post(PAGE)
def create_link(db: DB, user: Admin, csrf: CSRF,
                title: Annotated[str, Form(min_length=1, max_length=150)],
                utm_source: Annotated[str, Form(min_length=1, max_length=150)],
                utm_medium: Annotated[str, Form(min_length=1, max_length=150)],
                utm_campaign: Annotated[str, Form(min_length=1, max_length=150)],
                utm_content: Annotated[str, Form(max_length=150)] = ""):
    if not all(field.strip() for field in (title, utm_source, utm_medium, utm_campaign)):
        raise HTTPException(422, "Заполните название, источник, канал и кампанию")
    db.execute(text("""
        INSERT INTO bot_links (code, title, utm_source, utm_medium, utm_campaign, utm_content)
        VALUES (:code, :title, :utm_source, :utm_medium, :utm_campaign, :utm_content)
    """), dict(code=secrets.token_urlsafe(12), title=title.strip(), utm_source=utm_source.strip(),
               utm_medium=utm_medium.strip(), utm_campaign=utm_campaign.strip(), utm_content=utm_content.strip()))
    db.commit()
    return RedirectResponse(PAGE, status_code=303)


@router.post(PAGE + "/{code}/archive")
def archive(code: str, db: DB, user: Admin, csrf: CSRF):
    db.execute(text("UPDATE bot_links SET archived=true WHERE code=:code"), {"code": code})
    db.commit()
    return RedirectResponse(PAGE, status_code=303)


def csv_response(headers, rows, filename):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["'" + cell if isinstance(cell, str) and cell.lstrip().startswith(
            ("=", "+", "-", "@")) else cell for cell in row])
    return Response("\ufeff" + output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.csv"',
                             "Cache-Control": "no-store"})


@router.get(PAGE)
def report(request: Request, db: DB, user: ReportUser, since: date | None = None,
           until: date | None = None, code: str = "", source: str = "", campaign: str = "",
           uid: Annotated[int | None, Query(gt=0, lt=2**63)] = None,
           export: Literal["summary", "events"] | None = None):
    today = datetime.now(MSK).date()
    since, until = since or today - timedelta(days=29), until or today
    if until < since or until == date.max:
        raise HTTPException(400)
    start, end = (datetime.combine(day, time.min, MSK).astimezone(timezone.utc)
                  for day in (since, until + timedelta(days=1)))
    links = db.execute(text("SELECT * FROM bot_links ORDER BY archived, title")).mappings().all()
    selected = {link["code"]: link for link in links if
                (not code or link["code"] == code) and (not source or link["utm_source"] == source)
                and (not campaign or link["utm_campaign"] == campaign)}
    if not (source or campaign) and code in ("", "unknown"):
        selected[None] = {"title": "Источник неизвестен"}
    events = db.execute(text("""
        SELECT e.*, first.at AS first_at, first.code AS first_code
        FROM bot_events e LEFT JOIN bot_events first ON first.id=(
            SELECT MIN(s.id) FROM bot_events s WHERE s.uid=e.uid AND s.kind='start'
        )
        WHERE e.at >= :start AND e.at < :end ORDER BY e.id DESC
    """), dict(start=start, end=end)).mappings()
    counters = {key: {} for key in selected}
    history = []
    for event in events:
        kind = event["kind"]
        key = event["code"] if kind == "click" else event["first_code"]
        person = event["uid"]
        if kind == "start" and event["code"] is not None and event["code"] in selected:
            counters[event["code"]].setdefault("tagged_start", set()).add(person)
        if key not in selected:
            if kind == "start" and event["code"] is not None and event["code"] in selected:
                if (uid is None or person == uid) and (export == "events" or len(history) < 200):
                    history.append(event)
            continue
        metric = f"{kind}/{event['value']}" if kind in ("step", "situation", "material", "button") else kind
        group = counters[key]
        if kind == "click":
            group[metric] = group.get(metric, 0) + 1
        else:
            group.setdefault(metric, set()).add(person)
        if (uid is None or person == uid) and (export == "events" or len(history) < 200):
            history.append(event)
    summary = [(key, selected[key]["title"], metric, count if isinstance(count, int) else len(count))
               for key, metrics in counters.items() for metric, count in sorted(metrics.items())]
    if export == "summary":
        return csv_response(("code", "link", "metric", "count"), summary, "telegram-summary")
    if export == "events":
        columns = ("uid", "at", "kind", "value", "code", "first_at", "first_code")
        return csv_response(columns, ([event[name] for name in columns] for event in history), "telegram-events")
    filters = {k: v for k, v in dict(since=since, until=until, code=code, source=source,
                                    campaign=campaign, uid=uid).items() if v}
    profile = {}
    if uid is not None:
        rows = db.execute(text("""
            SELECT kind, value, at, code FROM bot_events
            WHERE uid=:uid AND kind IN ('start', 'situation', 'step') ORDER BY id
        """), {"uid": uid}).mappings()
        for row in rows:
            if row["kind"] != "start" or "start" not in profile:
                profile[row["kind"]] = row
    return templates.TemplateResponse(request, "bot_analytics.html", {
        "user": user, "can_edit_links": can_edit_links(user), "page": PAGE,
        "links": links, "summary": summary, "history": history,
        "filters": filters, "query": urlencode(filters), "profile": profile, "selected": selected,
    }, headers={"Cache-Control": "no-store"})
