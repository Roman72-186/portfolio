"""Shared aggregates for the Chief Teacher and Superadmin dashboards."""

import csv
import io
from datetime import date, datetime
from typing import TypedDict

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.models.activity_event import StudentActivityEvent
from app.constants import TARIFFS_CURRENT, TARIFF_DISPLAY
from app.models.role import Role
from app.models.session import Session as UserSession
from app.models.user import User
from app.models.work import Work, WORK_TYPE_AFTER, WORK_TYPE_BEFORE
from app.services.tz import msk_midnight


REGISTRATION_STATS_SINCE = date(2026, 9, 18)
# Владелец платформы зарегистрировался для проверки потока как ученик.
# По его просьбе тестовый аккаунт не входит в продуктовую статистику.
REGISTRATION_STATS_EXCLUDED_USER_IDS = frozenset({199})


class TariffRegistrationItem(TypedDict):
    tariff: str
    label: str
    count: int


class TariffRegistrationStats(TypedDict):
    since_label: str
    by_tariff: list[TariffRegistrationItem]
    without_tariff: int
    total: int
    students: list["TariffRegistrationStudent"]


class TariffRegistrationStudent(TypedDict):
    id: int
    name: str
    username: str
    tariff: str
    tariff_label: str
    created_at: datetime


def get_tariff_registration_stats(db: DBSession) -> TariffRegistrationStats:
    """Count student registrations since tariff tracking started in Moscow time.

    The metric records registrations, so inactive, archived and soft-deleted
    students remain in the aggregate. Staff accounts never enter it.
    """
    rows = (
        db.query(
            User.id,
            User.name,
            User.first_name,
            User.last_name,
            User.tg_username,
            User.tariff,
            User.created_at,
        )
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == 1,
            User.created_at >= msk_midnight(REGISTRATION_STATS_SINCE),
            User.id.notin_(REGISTRATION_STATS_EXCLUDED_USER_IDS),
        )
        .order_by(User.created_at.desc(), User.id.desc())
        .all()
    )
    raw_counts = {tariff: 0 for tariff in TARIFFS_CURRENT}
    without_tariff = 0
    students: list[TariffRegistrationStudent] = []
    for row in rows:
        tariff = (row.tariff or "").strip()
        if tariff in raw_counts:
            raw_counts[tariff] += 1
            tariff_label = TARIFF_DISPLAY[tariff]
        elif not tariff:
            without_tariff += 1
            tariff_label = "Без тарифа"
        else:
            # Неизвестный или старый тариф не превращаем в один из трёх
            # действующих. Запись остаётся в списке для ручной проверки.
            tariff_label = tariff

        username = (row.tg_username or "").strip().lstrip("@")
        students.append(
            {
                "id": row.id,
                "name": f"{row.last_name or ''} {row.first_name or row.name}".strip(),
                "username": f"@{username}" if username else "Не указан",
                "tariff": tariff,
                "tariff_label": tariff_label,
                "created_at": row.created_at,
            }
        )

    students.sort(key=lambda student: (student["tariff_label"].casefold(), student["name"].casefold()))

    by_tariff = [
        {
            "tariff": tariff,
            "label": TARIFF_DISPLAY[tariff],
            "count": raw_counts.get(tariff, 0),
        }
        for tariff in TARIFFS_CURRENT
    ]
    return {
        "since_label": REGISTRATION_STATS_SINCE.strftime("%d.%m.%Y"),
        "by_tariff": by_tariff,
        "without_tariff": without_tariff,
        "total": sum(item["count"] for item in by_tariff) + without_tariff,
        "students": students,
    }


def build_tariff_registration_csv(stats: TariffRegistrationStats) -> str:
    """Serialize the visible registration list for spreadsheet downloads."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Имя", "Username", "Тариф", "Дата регистрации"])
    for student in stats["students"]:
        created_at = student["created_at"]
        writer.writerow([
            student["name"],
            student["username"],
            student["tariff_label"],
            created_at.strftime("%d.%m.%Y %H:%M") if created_at else "",
        ])
    return "\ufeff" + output.getvalue()


_ACTIVITY_LABELS = {
    "login": "Вход в кабинет",
    "portfolio_upload": "Загрузка портфолио",
    "work_upload": "Загрузка работы",
}


def get_student_activity_overview(db: DBSession, event_limit: int = 200) -> dict:
    """Return per-student lifecycle metrics and the append-only action journal."""
    students = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == 1, User.id.notin_(REGISTRATION_STATS_EXCLUDED_USER_IDS))
        .order_by(User.last_name, User.first_name, User.id)
        .all()
    )
    student_ids = [student.id for student in students]
    if not student_ids:
        return {"students": [], "events": []}

    login_rows = (
        db.query(
            UserSession.user_id,
            func.min(UserSession.created_at).label("first_login"),
            func.max(UserSession.created_at).label("last_login"),
            func.count(UserSession.id).label("login_count"),
        )
        .filter(UserSession.user_id.in_(student_ids), UserSession.impersonated_by_id.is_(None))
        .group_by(UserSession.user_id)
        .all()
    )
    upload_rows = (
        db.query(
            Work.user_id,
            func.count(Work.id).label("upload_count"),
            func.min(Work.created_at).label("first_upload"),
            func.max(Work.created_at).label("last_upload"),
        )
        .filter(
            Work.user_id.in_(student_ids),
            Work.status == "success",
            Work.work_type.in_((WORK_TYPE_BEFORE, WORK_TYPE_AFTER)),
        )
        .group_by(Work.user_id)
        .all()
    )
    login_by_user = {row.user_id: row for row in login_rows}
    upload_by_user = {row.user_id: row for row in upload_rows}
    events = (
        db.query(StudentActivityEvent, User)
        .join(User, StudentActivityEvent.user_id == User.id)
        .filter(StudentActivityEvent.user_id.in_(student_ids))
        .order_by(StudentActivityEvent.created_at.desc(), StudentActivityEvent.id.desc())
        .limit(event_limit)
        .all()
    )
    latest_event_by_user: dict[int, StudentActivityEvent] = {}
    for event, _user in events:
        latest_event_by_user.setdefault(event.user_id, event)

    overview = []
    for student in students:
        login = login_by_user.get(student.id)
        upload = upload_by_user.get(student.id)
        latest_event = latest_event_by_user.get(student.id)
        overview.append({
            "id": student.id,
            "name": f"{student.last_name or ''} {student.first_name or student.name}".strip(),
            "username": f"@{(student.tg_username or '').strip().lstrip('@')}" if student.tg_username else "Не указан",
            "tariff": student.tariff or "Без тарифа",
            "registered_at": student.created_at,
            "first_login": login.first_login if login else None,
            "last_login": login.last_login if login else None,
            "login_count": int(login.login_count) if login else 0,
            "upload_count": int(upload.upload_count) if upload else 0,
            "first_upload": upload.first_upload if upload else None,
            "last_upload": upload.last_upload if upload else None,
            "last_action_at": latest_event.created_at if latest_event else (login.last_login if login else None),
            "last_action": _ACTIVITY_LABELS.get(latest_event.event_type, latest_event.event_type) if latest_event else ("Вход в кабинет" if login else "Нет действий"),
        })

    journal = [
        {
            "created_at": event.created_at,
            "student_name": f"{user.last_name or ''} {user.first_name or user.name}".strip(),
            "username": f"@{(user.tg_username or '').strip().lstrip('@')}" if user.tg_username else "Не указан",
            "event_type": event.event_type,
            "action": _ACTIVITY_LABELS.get(event.event_type, event.event_type),
            "details": event.details,
        }
        for event, user in events
    ]
    return {"students": overview, "events": journal}
