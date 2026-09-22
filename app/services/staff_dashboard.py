"""Shared aggregates for the Chief Teacher and Superadmin dashboards."""

import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import TypedDict

from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DBSession

from app.models.activity_event import StudentActivityEvent
from app.models.learning_topic import LearningTopic, LearningTopicAssignee, LearningTopicTag, LearningTopicTariff
from app.models.tag import UserTag
from app.models.task_block import TaskBlock, TaskBlockSubmission, TaskBlockTariff, SUBMISSION_BLOCK_TYPES
from app.models.tracker import TrackerTask, TrackerTaskAssignee, TrackerTaskTag
from app.constants import TARIFFS_CURRENT, TARIFF_DISPLAY
from app.models.role import Role
from app.models.session import Session as UserSession
from app.models.user import User
from app.models.work import Work, WORK_TYPE_AFTER, WORK_TYPE_BEFORE
from app.services.tz import msk_midnight


REGISTRATION_STATS_SINCE = date(2026, 9, 19)
# Владелец платформы зарегистрировался для проверки потока как ученик.
# По его просьбе тестовый аккаунт не входит в продуктовую статистику.
REGISTRATION_STATS_EXCLUDED_USER_IDS = frozenset({199})


def parse_registration_date(raw: str | None, fallback: date | None = None) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else fallback
    except ValueError:
        return fallback


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
    period_from: str
    period_to: str
    tariff_filter: str


class TariffRegistrationStudent(TypedDict):
    id: int
    name: str
    username: str
    tariff: str
    tariff_label: str
    created_at: datetime


def get_tariff_registration_stats(
    db: DBSession,
    *,
    period_from: date | None = None,
    period_to: date | None = None,
    tariff_filter: str = "",
) -> TariffRegistrationStats:
    """Count student registrations since tariff tracking started in Moscow time.

    The metric records registrations, so inactive, archived and soft-deleted
    students remain in the aggregate. Staff accounts never enter it.
    """
    period_from = period_from or REGISTRATION_STATS_SINCE
    query = (
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
            User.created_at >= msk_midnight(period_from),
            User.id.notin_(REGISTRATION_STATS_EXCLUDED_USER_IDS),
        )
    )
    if period_to:
        query = query.filter(User.created_at < msk_midnight(period_to + timedelta(days=1)))
    if tariff_filter in TARIFFS_CURRENT:
        query = query.filter(User.tariff == tariff_filter)
    elif tariff_filter == "__none__":
        query = query.filter(or_(User.tariff.is_(None), User.tariff == ""))
    rows = query.order_by(User.created_at.desc(), User.id.desc()).all()
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
        "since_label": period_from.strftime("%d.%m.%Y"),
        "by_tariff": by_tariff,
        "without_tariff": without_tariff,
        "total": sum(item["count"] for item in by_tariff) + without_tariff,
        "students": students,
        "period_from": period_from.isoformat(),
        "period_to": period_to.isoformat() if period_to else "",
        "tariff_filter": tariff_filter,
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


def _assignment_activity(db: DBSession, students: list[User]) -> list[dict]:
    """Published upload blocks with their intended audience and actual submissions."""
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(TaskBlock, TrackerTask, LearningTopic)
        .join(TrackerTask, TaskBlock.task_id == TrackerTask.id)
        .outerjoin(LearningTopic, TrackerTask.topic_id == LearningTopic.id)
        .filter(
            TaskBlock.block_type.in_(SUBMISSION_BLOCK_TYPES),
            TrackerTask.is_published.is_(True),
            TrackerTask.deleted_at.is_(None),
            or_(TrackerTask.starts_at.is_(None), TrackerTask.starts_at <= now),
            or_(TaskBlock.opens_at.is_(None), TaskBlock.opens_at <= now),
            or_(TrackerTask.topic_id.is_(None),
                (LearningTopic.is_published.is_(True)) &
                (LearningTopic.deleted_at.is_(None)) &
                (LearningTopic.opens_at <= now)),
        )
        .order_by(TrackerTask.id.desc(), TaskBlock.sort_order, TaskBlock.id)
        .all()
    )
    if not candidates:
        return []

    block_ids = [block.id for block, _, _ in candidates]
    task_ids = {task.id for _, task, _ in candidates}
    topic_ids = {topic.id for _, _, topic in candidates if topic}
    student_ids = [student.id for student in students]
    tags_by_user: dict[int, set[int]] = {}
    for user_id, tag_id in db.query(UserTag.user_id, UserTag.tag_id).filter(UserTag.user_id.in_(student_ids)):
        tags_by_user.setdefault(user_id, set()).add(tag_id)

    def pairs(owner_column, value_column, owner_ids):
        result: dict[int, set] = {}
        for owner, value in db.query(owner_column, value_column).filter(owner_column.in_(owner_ids)):
            result.setdefault(owner, set()).add(value)
        return result

    task_tags = pairs(TrackerTaskTag.task_id, TrackerTaskTag.tag_id, task_ids)
    task_assignees = pairs(TrackerTaskAssignee.task_id, TrackerTaskAssignee.user_id, task_ids)
    topic_tags = pairs(LearningTopicTag.topic_id, LearningTopicTag.tag_id, topic_ids)
    topic_assignees = pairs(LearningTopicAssignee.topic_id, LearningTopicAssignee.user_id, topic_ids)
    topic_tariffs = pairs(LearningTopicTariff.topic_id, LearningTopicTariff.tariff, topic_ids)
    block_tariffs = pairs(TaskBlockTariff.block_id, TaskBlockTariff.tariff, block_ids)
    submitted_by_block: dict[int, set[int]] = {}
    for block_id, user_id in (
        db.query(TaskBlockSubmission.block_id, TaskBlockSubmission.user_id)
        .filter(TaskBlockSubmission.block_id.in_(block_ids),
                TaskBlockSubmission.user_id.in_(student_ids),
                TaskBlockSubmission.submitted_at.isnot(None))
    ):
        submitted_by_block.setdefault(block_id, set()).add(user_id)

    assignments = []
    for block, task, topic in candidates:
        eligible = []
        for student in students:
            tags = tags_by_user.get(student.id, set())
            if topic:
                addressed = (topic.assign_to_all or student.id in topic_assignees.get(topic.id, set())
                             or bool(tags & topic_tags.get(topic.id, set())))
                tariff_allowed = (not topic.tariff_restricted or
                                  (student.tariff or "").strip().upper() in topic_tariffs.get(topic.id, set()))
            else:
                addressed = (task.assign_to_all or student.id in task_assignees.get(task.id, set())
                             or bool(tags & task_tags.get(task.id, set())))
                tariff_allowed = True
            if addressed and tariff_allowed and (not block_tariffs.get(block.id) or
                                                  student.tariff in block_tariffs[block.id]):
                eligible.append(student.id)
        label = task.title
        if block.title and block.title.strip() and block.title.strip().casefold() != task.title.casefold():
            label += f" · {block.title.strip()}"
        assignments.append({
            "id": block.id,
            "label": label,
            "eligible": eligible,
            "submitted": sorted(submitted_by_block.get(block.id, set())),
        })
    return assignments


def get_student_activity_overview(db: DBSession, event_limit: int = 200, *, include_assignments: bool = False) -> dict:
    """Return per-student lifecycle metrics and the append-only action journal."""
    students = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == 1,
            User.id.notin_(REGISTRATION_STATS_EXCLUDED_USER_IDS),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
        )
        .order_by(User.last_name, User.first_name, User.id)
        .all()
    )
    student_ids = [student.id for student in students]
    if not student_ids:
        return {"students": [], "events": [], "assignments": []}

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
    portfolio_before_user_ids = {
        row.user_id
        for row in (
            db.query(Work.user_id)
            .filter(
                Work.user_id.in_(student_ids),
                Work.status == "success",
                Work.work_type == WORK_TYPE_BEFORE,
            )
            .distinct()
            .all()
        )
    }
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
            "has_portfolio_before": student.id in portfolio_before_user_ids,
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
    return {"students": overview, "events": journal,
            "assignments": _assignment_activity(db, students) if include_assignments else []}
