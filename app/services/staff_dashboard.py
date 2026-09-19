"""Shared aggregates for the Chief Teacher and Superadmin dashboards."""

import csv
import io
from datetime import date, datetime
from typing import TypedDict

from sqlalchemy.orm import Session as DBSession

from app.constants import TARIFFS_CURRENT, TARIFF_DISPLAY
from app.models.role import Role
from app.models.user import User
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
