"""Shared aggregates for the Chief Teacher and Superadmin dashboards."""

import csv
import io
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import TypedDict

from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DBSession, aliased

from app.models.activity_event import StudentActivityEvent
from app.models.learning_topic import LearningTopic
from app.models.task_block import TaskBlock, TaskBlockState, TaskBlockSubmission, SUBMISSION_BLOCK_TYPES
from app.models.tracker import STATUS_DONE, TrackerTask
from app.constants import REPORT_EXCLUDED_USER_IDS, TARIFFS_CURRENT, TARIFF_DISPLAY
from app.models.role import Role
from app.models.session import Session as UserSession
from app.models.user import User
from app.models.work import Work, WORK_TYPE_AFTER, WORK_TYPE_BEFORE
from app.services.submission_edit import upload_deadline
from app.services.task_blocks import (
    completed_after_deadline, feed_visible_blocks, get_submit_deadlines,
    get_task_submit_deadlines, get_tariffs,
)
from app.services.tracker import (
    cycle_deadline_lookup, program_learners, program_students, task_audience_user_ids,
)
from app.services.tz import msk_midnight, msk_text


REGISTRATION_STATS_SINCE = date(2026, 9, 19)


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
            User.id.notin_(REPORT_EXCLUDED_USER_IDS),
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
    """Открытые блоки сдачи работы (домашка и контрольная на время): кому
    положено сдавать, кто сдал, а у несдавших — прошёл ли срок.

    Фильтр «Задание → Сдали / Не сдали» в «Учениках поимённо» и копирование
    списка (владелец 03.10.2026: «выбрал задание, фильтр сдал или не сдал, и
    показался список, который можно скопировать»).

    Правила не свои — те же, что у напоминаний о сроке
    (`student_reminders._collect_deadlines`): кому видно задание —
    `tracker.task_audience_user_ids`, с кого спрашивают —
    `program_students`/`program_learners`, блок по тарифу —
    `feed_visible_blocks`, сдал — `TaskBlockState` в статусе «сделано», срок —
    `submission_edit.upload_deadline`. До 03.10.2026 здесь жила своя копия
    адресации, и в «не сдали» по каждой контрольной попадали шестеро
    пробников, у которых доступ кончился 27.09: бот им уже не напоминал,
    а статистика числила должниками.
    """
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(TaskBlock, TrackerTask)
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

    learners = program_learners(program_students(db, now))
    # Строки таблицы — свои у сводки; адресат без строки в списке не нужен.
    reachable = learners.keys() & {student.id for student in students}
    block_ids = [block.id for block, _ in candidates]
    task_ids = list({task.id for _, task in candidates})
    tariffs = get_tariffs(db, block_ids)
    block_deadlines = get_submit_deadlines(db, block_ids)
    task_deadlines = get_task_submit_deadlines(db, task_ids)
    # Срок цикла — запасной срок отметки «Сдал после срока» (06.10.2026).
    cycle_deadline = cycle_deadline_lookup(db, {task.topic_id for _, task in candidates})
    audience = {task_id: task_audience_user_ids(db, task_id) & reachable for task_id in task_ids}
    done = {
        (state.block_id, state.user_id): state
        for state in db.query(TaskBlockState).filter(
            TaskBlockState.block_id.in_(block_ids),
            TaskBlockState.status == STATUS_DONE,
        )
    }

    assignments = []
    for block, task in candidates:
        eligible = sorted(
            uid for uid in audience[task.id]
            if feed_visible_blocks([block], tariffs, learners[uid].tariff)
        )
        submitted, overdue, pending, notes = [], [], [], {}
        for uid in eligible:
            tariff = learners[uid].tariff
            state = done.get((block.id, uid))
            if state is not None:
                submitted.append(uid)
                late = completed_after_deadline(
                    block, task, state, user_tariff=tariff,
                    block_overrides=block_deadlines.get(block.id),
                    task_overrides=task_deadlines.get(task.id),
                    cycle_deadline=cycle_deadline(task.topic_id, tariff),
                )
                notes[uid] = "Сдал после срока" if late else "Сдал"
                continue
            deadline = upload_deadline(
                task, block, user_tariff=tariff,
                tariff_deadlines=block_deadlines.get(block.id),
                task_tariff_deadlines=task_deadlines.get(task.id),
            )
            if deadline is not None and deadline <= now:
                overdue.append(uid)
                notes[uid] = f"Не сдал · срок прошёл {msk_text(deadline)}"
            else:
                pending.append(uid)
                notes[uid] = (
                    f"Не сдал · срок до {msk_text(deadline)}" if deadline is not None
                    else "Не сдал · срок не задан"
                )
        label = task.title
        if block.title and block.title.strip() and block.title.strip().casefold() != task.title.casefold():
            label += f" · {block.title.strip()}"
        # Предмет — подпись в выпадающем списке, на подсчёт не влияет. Стоит
        # первым: на телефоне длинное название обрезается справа.
        subject = block.subject or task.subject
        if subject:
            label = f"{subject}: {label}"
        assignments.append({
            "id": block.id,
            "label": label,
            "eligible": eligible,
            "submitted": submitted,
            "overdue": overdue,
            "pending": pending,
            "notes": notes,
        })
    return assignments


def get_student_activity_overview(db: DBSession, event_limit: int = 200, *, include_assignments: bool = False) -> dict:
    """Return per-student lifecycle metrics and the append-only action journal."""
    students = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == 1,
            User.id.notin_(REPORT_EXCLUDED_USER_IDS),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
        )
        .order_by(User.last_name, User.first_name, User.id)
        .all()
    )
    student_ids = [student.id for student in students]
    if not student_ids:
        return {"students": [], "events": [], "assignments": [], "scores": []}

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
    assignments = _assignment_activity(db, students) if include_assignments else []
    return {"students": overview, "events": journal, "assignments": assignments,
            "scores": _assignment_scores(db, student_ids, assignments) if include_assignments else []}


def _score_value(score) -> int | float:
    """`55.00` → 55, `57.50` → 57.5: в списке, сводке и копии без хвоста нулей."""
    value = float(score)
    return int(value) if value.is_integer() else value


def _assignment_scores(db: DBSession, student_ids: list[int], assignments: list[dict]) -> list[dict]:
    """Баллы за сдачи по заданию — карточка «Баллы за задания» на «Статистике
    активности» (владелец 03.10.2026: «сколько людей на 55, сколько на 65» по
    контрольной).

    Задания и подписи — из `_assignment_activity`, своей выборки «какие
    задания открыты» здесь нет. Считаются все сдавшие из строк сводки, без
    фильтра `eligible`: у пробника с кончившимся доступом работа сдана и
    оценена, и в распределении балл быть должен. Само распределение по
    баллам строит страница из `scored` — так же, как разбивку по тарифам
    в «Учениках поимённо». Задание без единого балла не отдаётся.
    """
    if not assignments or not student_ids:
        return []
    scorer_user = aliased(User)
    rows = (
        db.query(TaskBlockSubmission, scorer_user)
        .outerjoin(scorer_user, TaskBlockSubmission.scored_by_id == scorer_user.id)
        .filter(
            TaskBlockSubmission.block_id.in_([item["id"] for item in assignments]),
            TaskBlockSubmission.user_id.in_(student_ids),
            TaskBlockSubmission.submitted_at.isnot(None),
        )
        .all()
    )
    by_block: dict[int, list[tuple[TaskBlockSubmission, User | None]]] = {}
    for submission, scorer in rows:
        by_block.setdefault(submission.block_id, []).append((submission, scorer))

    result = []
    for item in assignments:
        scored, scorers, unscored = {}, {}, []
        for submission, scorer in by_block.get(item["id"], []):
            if submission.score is None:
                unscored.append(submission.user_id)
                continue
            scored[submission.user_id] = _score_value(submission.score)
            if scorer is not None:
                scorers[submission.user_id] = (
                    f"{scorer.last_name or ''} {scorer.first_name or scorer.name or ''}".strip()
                )
        if not scored:
            continue
        values = list(scored.values())
        result.append({
            "id": item["id"],
            "label": item["label"],
            "submitted": len(scored) + len(unscored),
            "scored": scored,
            "unscored": sorted(unscored),
            "scorers": scorers,
            "avg": round(sum(values) / len(values), 1),
            "median": _score_value(median(values)),
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Главный экран Главного преподавателя и суперадмина
# ═══════════════════════════════════════════════════════════════════════════════

_MONTHS_PREP = ["", "январе", "феврале", "марте", "апреле", "мае", "июне",
                "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"]


def load_staff_dashboard(db: DBSession, user: dict, now: datetime) -> dict:
    """Данные для `cabinet_staff.html` — один экран у ГП и суперадмина.

    До 28.09.2026 жили двумя почти одинаковыми копиями в `cabinet_admin.py` и
    `cabinet_superadmin.py` и расходились по мелочам. Здесь же сняты расчёты,
    которые шаблон не показывал: регистрации и активность учеников (их
    карточки живут на «Статистике активности»), лента последних загрузок
    (ветка шаблона для ранга ниже 4, до которой ни один маршрут не доходит)
    и окно загрузки портфолио — с 09.09.2026 оно ни на что не влияет
    (`docs/invariants/portfolio.md`), а кнопка на дашборде им управляла.

    Всё учебное считается по ученикам (ранг 1): в «Учениках» и «Новых
    учениках» сотрудники не нужны. Аккаунт владельца (`REPORT_EXCLUDED_USER_IDS`)
    исключён отовсюду.

    Очереди пробников на дашборде нет (владелец 29.09.2026 снял блок «Ждёт
    проверки» вместе со ссылками на проверку, билеты и статистику пробников).
    Счётчика «ученики без точки А» тоже нет: `point_a.student_point_a` ходит в
    базу по каждому ученику, на 20 учениках дашборд делал 173 запроса вместо 14
    (`test_superadmin_dashboard_does_not_scale_with_data`).
    """
    from app.models.work import WORK_TYPE_MOCK_EXAM

    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    not_owner = User.id.notin_(REPORT_EXCLUDED_USER_IDS)

    role_rows = (
        db.query(Role.display_name, Role.rank, func.count(User.id).label("cnt"))
        .outerjoin(User, (User.role_id == Role.id) & (User.is_active == True) & not_owner)  # noqa: E712
        .group_by(Role.id, Role.display_name, Role.rank)
        .order_by(Role.rank)
        .all()
    )
    role_breakdown = [{"name": r.display_name, "rank": r.rank, "count": r.cnt} for r in role_rows]
    students_active = sum(r["count"] for r in role_breakdown if r["rank"] == 1)

    student_filter = (
        db.query(User.id)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == 1, not_owner)
    )
    students_blocked = (
        student_filter.filter(User.is_active == False, User.archived_at.is_(None))  # noqa: E712
        .count()
    )
    new_students_month = student_filter.filter(User.created_at >= month_start).count()

    works = db.query(Work).filter(Work.status == "success", Work.user_id.notin_(REPORT_EXCLUDED_USER_IDS))
    works_by_type = dict(
        works.with_entities(Work.work_type, func.count(Work.id)).group_by(Work.work_type).all()
    )
    total_works = sum(works_by_type.values())
    works_this_month = works.filter(Work.created_at >= month_start).count()

    avg_raw = (
        works.filter(Work.work_type == WORK_TYPE_MOCK_EXAM, Work.score.isnot(None))
        .with_entities(func.avg(Work.score))
        .scalar()
    )
    avg_mock_score = round(float(avg_raw)) if avg_raw is not None else None

    StudentAlias = aliased(User)
    curator_rows = (
        db.query(
            User.id, User.first_name, User.last_name, User.name, User.photo_url,
            func.count(StudentAlias.id).label("student_count"),
        )
        .join(Role, User.role_id == Role.id)
        .outerjoin(
            StudentAlias,
            (StudentAlias.curator_id == User.id)
            & (StudentAlias.is_active == True)  # noqa: E712
            & StudentAlias.id.notin_(REPORT_EXCLUDED_USER_IDS),
        )
        .filter(Role.rank == 2, User.is_active == True)  # noqa: E712
        .group_by(User.id, User.first_name, User.last_name, User.name, User.photo_url)
        .order_by(func.count(StudentAlias.id).desc())
        .limit(100)
        .all()
    )
    curators = [
        {
            "id": r.id,
            "name": f"{r.last_name or ''} {r.first_name or r.name}".strip(),
            "photo_url": r.photo_url,
            "student_count": r.student_count,
        }
        for r in curator_rows
    ]

    # Модератор (ранг 3) в списке рядом с ГП: уровень у него тот же
    # (rbac.py::effective_role_rank), подпись — своя роль.
    admin_rows = (
        db.query(User.id, User.first_name, User.last_name, User.name, User.photo_url, Role.display_name)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank.in_([3, 4]), User.is_active == True)  # noqa: E712
        .order_by(User.last_name, User.first_name)
        .limit(50)
        .all()
    )
    admins = [
        {
            "id": r.id,
            "name": f"{r.last_name or ''} {r.first_name or r.name}".strip(),
            "photo_url": r.photo_url,
            "role_label": r.display_name,
        }
        for r in admin_rows
    ]

    return {
        "role_breakdown": role_breakdown,
        "students_active": students_active,
        "students_blocked": students_blocked,
        "new_students_month": new_students_month,
        "works_by_type": works_by_type,
        "total_works": total_works,
        "works_this_month": works_this_month,
        "avg_mock_score": avg_mock_score,
        "curators": curators,
        "admins": admins,
        "month_name": _MONTHS_PREP[now.month],
    }
