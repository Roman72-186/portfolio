"""
Статистика активности учеников и кураторов для кабинета суперадмина.

Источники — таймстемпы, добавленные миграциями b5c6d7e8f9a0…f9a0b1c2d3e4
(11.07.2026): last_login_at, read_at, revision_done_at, viewed_at и аудит
curator_assign/tariff_change. Исторических данных до этой даты нет — страница
честно показывает «—», пока метрика не накопилась.

Агрегаты по датам считаем на Python-стороне (не func.avg над разностью
дат) — так запросы работают одинаково в Postgres и SQLite-тестах,
как в period_stats.py.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session as DBSession

from app.models.activity_event import StudentActivityEvent
from app.models.audit_log import AuditLog
from app.models.curator_report import CuratorReport
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.homework_feedback import HomeworkFeedbackMessage
from app.models.homework_submission import HomeworkSubmission, SUBMISSION_STATUSES
from app.models.learning_video import LearningVideo
from app.models.login_token import LoginToken
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.notification import Notification
from app.models.role import Role
from app.models.task_block import TaskBlockAnswer, TaskBlockSubmission
from app.models.task_block_feedback import TaskBlockFeedbackMessage
from app.models.tracker import TrackerTask, TrackerTaskState
from app.models.user import User
from app.models.video_progress import VideoProgress
from app.models.video_view_log import VideoViewLog
from app.models.work import Work, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.feedback import ROLE_STUDENT
from app.services.tz import MSK_TZ, msk_midnight

# Дата деплоя миграций — раньше неё новых таймстемпов не существует
ACTIVITY_STATS_START = datetime(2026, 7, 11, tzinfo=timezone.utc)

# Окно для журналов, которые только растут (входы, открытия видео, действия
# в аудите): считать их за всё время и медленно, и бессмысленно.
RECENT_DAYS = 30

# Вкладки страницы по ролям. Ранг → вкладка переводится только здесь: шаблон
# делит строки по `role_group` и сам чисел рангов не знает. Модератор (ранг 3)
# работает с правами Главного преподавателя, поэтому в одной вкладке с ним.
ROLE_GROUP_CURATORS = "curators"
ROLE_GROUP_HEAD = "head"
ROLE_GROUP_SUPERADMIN = "superadmin"


def role_group(rank: int | None) -> str | None:
    """Вкладка сотрудника на странице статистики; у ученика — None."""
    if rank == 2:
        return ROLE_GROUP_CURATORS
    if rank in (3, 4):
        return ROLE_GROUP_HEAD
    if rank is not None and rank >= 5:
        return ROLE_GROUP_SUPERADMIN
    return None


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite может вернуть naive datetime — считаем его UTC (как в period_stats)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _msk(dt: datetime | None) -> datetime | None:
    dt = _utc(dt)
    return dt.astimezone(MSK_TZ) if dt else None


def fmt_duration(seconds: float | None) -> str | None:
    """86400 → «1 д. 0 ч.», 4500 → «1 ч. 15 м.», 300 → «5 м.»."""
    if seconds is None:
        return None
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} д. {hours} ч."
    if hours:
        return f"{hours} ч. {minutes} м."
    return f"{minutes} м."


def _avg_seconds(pairs: list[tuple[datetime | None, datetime | None]]) -> float | None:
    """Средняя разность (later - earlier) в секундах; пары с None пропускаются."""
    deltas = []
    for earlier, later in pairs:
        earlier, later = _utc(earlier), _utc(later)
        if earlier is None or later is None:
            continue
        deltas.append((later - earlier).total_seconds())
    if not deltas:
        return None
    return sum(deltas) / len(deltas)


def _student_name(first_name, last_name, name) -> str:
    return f"{last_name or ''} {first_name or name}".strip()


def _active_students_q(db: DBSession):
    return (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == 1, User.is_active == True, User.deleted_at.is_(None))  # noqa: E712
    )


def _student_ids(db: DBSession):
    """Подзапрос id активных учеников для фильтра `col.in_(...)`.

    Входы, просмотры видео, открытие заданий и уведомления пишутся и у
    сотрудников — без этого фильтра ученические цифры молча смешались бы
    с кураторскими.
    """
    return _active_students_q(db).with_entities(User.id).scalar_subquery()


def get_login_stats(db: DBSession) -> dict:
    """Входы учеников: за 7/30 дней, всего активных, список давно не заходивших.

    last_login_at копится с ACTIVITY_STATS_START: NULL означает
    «не заходил с этой даты», а не «никогда».
    """
    now = datetime.now(timezone.utc)
    total = _active_students_q(db).count()
    d7 = _active_students_q(db).filter(User.last_login_at >= now - timedelta(days=7)).count()
    d30 = _active_students_q(db).filter(User.last_login_at >= now - timedelta(days=30)).count()

    inactive_rows = (
        _active_students_q(db)
        .filter(or_(
            User.last_login_at.is_(None),
            User.last_login_at < now - timedelta(days=14),
        ))
        .order_by(User.last_login_at.asc().nullsfirst(), User.last_name, User.first_name)
        .limit(200)
        .all()
    )
    inactive = [
        {
            "student_id": u.id,
            "student_name": _student_name(u.first_name, u.last_name, u.name),
            "tg_username": (u.tg_username or "").lstrip("@"),
            "tariff": u.tariff or "—",
            "last_login_at": _msk(u.last_login_at),
        }
        for u in inactive_rows
    ]
    return {"total": total, "d7": d7, "d30": d30, "inactive": inactive}


def get_curator_review_speed(db: DBSession) -> list[dict]:
    """Скорость проверки работ: по каждому проверяющему — сколько оценил,
    среднее время от загрузки до оценки, средний балл.

    scored_at исторический (писался и до 11.07), так что метрика доступна
    ретроспективно. Учитываются все оценённые работы (mock_exam и retake).
    """
    rows = (
        db.query(Work.scored_by_id, Work.created_at, Work.scored_at, Work.score)
        .filter(Work.scored_at.isnot(None), Work.scored_by_id.isnot(None))
        .all()
    )
    by_curator: dict[int, list] = defaultdict(list)
    for r in rows:
        by_curator[r.scored_by_id].append(r)

    names: dict[int, tuple[str, int]] = {}
    if by_curator:
        for u in db.query(User).filter(User.id.in_(by_curator.keys())).all():
            rank = u.role.rank if u.role else 0
            names[u.id] = (_student_name(u.first_name, u.last_name, u.name), rank)

    result = []
    for cid, items in by_curator.items():
        avg_sec = _avg_seconds([(r.created_at, r.scored_at) for r in items])
        scores = [float(r.score) for r in items if r.score is not None]
        name, rank = names.get(cid, (f"id={cid}", 0))
        result.append({
            "curator_id": cid,
            "curator_name": name,
            "role_rank": rank,
            "role_group": role_group(rank),
            "scored_count": len(items),
            "avg_review_seconds": avg_sec,
            "avg_review_text": fmt_duration(avg_sec),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        })
    result.sort(key=lambda r: r["scored_count"], reverse=True)
    return result


def get_notification_reaction(db: DBSession) -> dict:
    """Время реакции учеников на уведомления (read_at копится с 11.07.2026).

    Только ученики: уведомления получают и сотрудники, их скорость чтения —
    другой вопрос и в ученическую вкладку не подмешивается.
    """
    student_ids = _student_ids(db)
    read_pairs = (
        db.query(Notification.created_at, Notification.read_at)
        .filter(Notification.read_at.isnot(None), Notification.user_id.in_(student_ids))
        .all()
    )
    unread_total = (
        db.query(Notification)
        .filter(Notification.is_read == False, Notification.user_id.in_(student_ids))  # noqa: E712
        .count()
    )
    avg_sec = _avg_seconds([(c, r) for c, r in read_pairs])
    return {
        "read_count": len(read_pairs),
        "unread_count": unread_total,
        "avg_reaction_seconds": avg_sec,
        "avg_reaction_text": fmt_duration(avg_sec),
    }


def get_revision_stats(db: DBSession) -> dict:
    """Возвраты циклов на правку ОС: висящие сейчас + среднее время правки.

    Длительность правки считается только по возвратам, завершённым после
    11.07.2026 (revision_done_at появился этой миграцией).
    """
    pending_rows = (
        db.query(ExamCycle, User)
        .join(User, ExamCycle.user_id == User.id)
        .filter(
            ExamCycle.revision_requested_at.isnot(None),
            ExamCycle.revision_done_at.is_(None),
        )
        .order_by(ExamCycle.revision_requested_at.asc())
        .limit(100)
        .all()
    )
    pending = [
        {
            "cycle_id": c.id,
            "subject": c.subject,
            "student_name": _student_name(u.first_name, u.last_name, u.name),
            "requested_at": _msk(c.revision_requested_at),
        }
        for c, u in pending_rows
    ]
    done_pairs = (
        db.query(ExamCycle.revision_requested_at, ExamCycle.revision_done_at)
        .filter(ExamCycle.revision_done_at.isnot(None))
        .all()
    )
    avg_sec = _avg_seconds([(req, done) for req, done in done_pairs])
    return {
        "pending": pending,
        "done_count": len(done_pairs),
        "avg_fix_seconds": avg_sec,
        "avg_fix_text": fmt_duration(avg_sec),
    }


def get_onboarding_funnel(db: DBSession) -> dict:
    """Воронка онбординга активных учеников + средняя длительность шага
    (по profile_completed_at / portfolio_do_completed_at, копятся с 11.07)."""
    students = _active_students_q(db).all()
    total = len(students)
    profile_done = sum(1 for s in students if s.profile_completed)
    portfolio_done = sum(1 for s in students if s.portfolio_do_completed)
    needs_setup = sum(1 for s in students if not s.profile_completed)

    profile_sec = _avg_seconds([
        (s.created_at, s.profile_completed_at) for s in students if s.profile_completed_at
    ])
    portfolio_sec = _avg_seconds([
        (s.created_at, s.portfolio_do_completed_at) for s in students if s.portfolio_do_completed_at
    ])
    return {
        "total": total,
        "profile_done": profile_done,
        "portfolio_done": portfolio_done,
        "needs_setup": needs_setup,
        "avg_profile_text": fmt_duration(profile_sec),
        "avg_portfolio_text": fmt_duration(portfolio_sec),
    }


def get_report_view_stats(db: DBSession) -> dict:
    """Видео-отчёты кураторов: сколько просмотрено staff'ом и как быстро."""
    rows = db.query(CuratorReport.created_at, CuratorReport.viewed_at).all()
    total = len(rows)
    viewed = [(c, v) for c, v in rows if v is not None]
    avg_sec = _avg_seconds(viewed)
    return {
        "total": total,
        "viewed_count": len(viewed),
        "avg_view_seconds": avg_sec,
        "avg_view_text": fmt_duration(avg_sec),
    }


def get_mock_attempt_stats(db: DBSession) -> dict:
    """Поведение ученика на пробнике: от получения билета до сдачи.

    Данные исторические (MockExamAttempt ведётся давно): среднее время
    started_at→completed_at, доля «сгоревших» (expired_at) и незавершённых.
    """
    rows = db.query(
        MockExamAttempt.started_at,
        MockExamAttempt.completed_at,
        MockExamAttempt.expired_at,
        MockExamAttempt.subject,
    ).all()
    total = len(rows)
    completed = [(r.started_at, r.completed_at) for r in rows if r.completed_at is not None]
    expired = sum(1 for r in rows if r.expired_at is not None and r.completed_at is None)
    avg_sec = _avg_seconds(completed)

    by_subject: dict[str, dict] = {}
    for subj in sorted({r.subject for r in rows}):
        subj_rows = [r for r in rows if r.subject == subj]
        subj_completed = [(r.started_at, r.completed_at) for r in subj_rows if r.completed_at]
        by_subject[subj] = {
            "total": len(subj_rows),
            "completed": len(subj_completed),
            "avg_text": fmt_duration(_avg_seconds(subj_completed)),
        }
    return {
        "total": total,
        "completed_count": len(completed),
        "expired_count": expired,
        "avg_seconds": avg_sec,
        "avg_text": fmt_duration(avg_sec),
        "by_subject": by_subject,
    }


def get_cycle_duration_stats(db: DBSession) -> dict:
    """Длительность цикла Пробника: от старта (дата, 00:00 MSK) до закрытия."""
    rows = db.query(ExamCycle.started_at, ExamCycle.closed_at).all()
    open_count = sum(1 for r in rows if r.closed_at is None)
    closed_pairs = [
        (msk_midnight(r.started_at), r.closed_at) for r in rows if r.closed_at is not None
    ]
    avg_sec = _avg_seconds(closed_pairs)
    return {
        "total": len(rows),
        "open_count": open_count,
        "closed_count": len(closed_pairs),
        "avg_seconds": avg_sec,
        "avg_text": fmt_duration(avg_sec),
    }


def get_feedback_curator_stats(db: DBSession) -> list[dict]:
    """ОС-метрики по кураторам: диалогов, сообщений на диалог, скорость первой ОС.

    Скорость первой ОС = от загрузки работы (Work.created_at) до первого
    staff-сообщения в диалоге. Данные исторические.
    """
    fb_rows = (
        db.query(Feedback.id, Feedback.curator_id, Work.created_at)
        .join(Work, Feedback.work_id == Work.id)
        .all()
    )
    if not fb_rows:
        return []
    msgs_by_fb: dict[int, list] = defaultdict(list)
    for m in (
        db.query(FeedbackMessage.feedback_id, FeedbackMessage.sender_role, FeedbackMessage.created_at)
        .order_by(FeedbackMessage.created_at, FeedbackMessage.id)
        .all()
    ):
        msgs_by_fb[m.feedback_id].append(m)

    by_curator: dict[int, dict] = defaultdict(lambda: {"dialogs": 0, "messages": 0, "first_pairs": []})
    for fb_id, curator_id, work_created in fb_rows:
        agg = by_curator[curator_id]
        agg["dialogs"] += 1
        msgs = msgs_by_fb.get(fb_id, [])
        agg["messages"] += len(msgs)
        first_staff = next((m for m in msgs if m.sender_role != ROLE_STUDENT), None)
        if first_staff is not None:
            agg["first_pairs"].append((work_created, first_staff.created_at))

    names: dict[int, tuple[str, int]] = {}
    for uid, fn, ln, nm, rank in (
        db.query(User.id, User.first_name, User.last_name, User.name, Role.rank)
        .outerjoin(Role, User.role_id == Role.id)
        .filter(User.id.in_(by_curator.keys()))
        .all()
    ):
        names[uid] = (_student_name(fn, ln, nm), rank or 0)

    result = []
    for cid, agg in by_curator.items():
        first_sec = _avg_seconds(agg["first_pairs"])
        name, rank = names.get(cid, (f"id={cid}", 0))
        result.append({
            "curator_id": cid,
            "curator_name": name,
            "role_rank": rank,
            "role_group": role_group(rank),
            "dialogs": agg["dialogs"],
            "avg_messages": round(agg["messages"] / agg["dialogs"], 1) if agg["dialogs"] else None,
            "avg_first_response_seconds": first_sec,
            "avg_first_response_text": fmt_duration(first_sec),
        })
    result.sort(key=lambda r: r["dialogs"], reverse=True)
    return result


def get_retake_stats(db: DBSession) -> dict:
    """Пересдачи и доработки: доля отправленных на отработку/доработку,
    среднее число попыток в цикле."""
    mock_total = (
        db.query(Work)
        .filter(Work.work_type == WORK_TYPE_MOCK_EXAM, Work.status == "success")
        .count()
    )
    retake_total = (
        db.query(Work)
        .filter(Work.work_type == WORK_TYPE_RETAKE, Work.status == "success")
        .count()
    )
    sent_to_retake = (
        db.query(Work)
        .filter(Work.work_type == WORK_TYPE_MOCK_EXAM, Work.sent_to_retake == True)  # noqa: E712
        .count()
    )
    needs_revision = (
        db.query(Work)
        .filter(Work.needs_revision == True)  # noqa: E712
        .count()
    )
    attempt_numbers = [
        n for (n,) in db.query(Work.attempt_number)
        .filter(Work.is_final == True, Work.attempt_number.isnot(None))  # noqa: E712
        .all()
    ]
    return {
        "mock_total": mock_total,
        "retake_total": retake_total,
        "sent_to_retake": sent_to_retake,
        "sent_to_retake_pct": round(100 * sent_to_retake / mock_total, 1) if mock_total else None,
        "needs_revision": needs_revision,
        "avg_attempts": round(sum(attempt_numbers) / len(attempt_numbers), 1) if attempt_numbers else None,
        "max_attempts": max(attempt_numbers) if attempt_numbers else None,
    }


def get_login_link_stats(db: DBSession) -> dict:
    """Конверсия одноразовых login-ссылок: выдано → использовано, скорость входа.

    Отозванных много by design: при выдаче новой ссылки прежние авто-отзываются.
    """
    rows = db.query(LoginToken.created_at, LoginToken.used_at, LoginToken.revoked_at).all()
    total = len(rows)
    used = [(c, u) for c, u, _ in rows if u is not None]
    revoked = sum(1 for _, u, r in rows if r is not None and u is None)
    avg_sec = _avg_seconds(used)
    return {
        "total": total,
        "used_count": len(used),
        "revoked_count": revoked,
        "conversion_pct": round(100 * len(used) / total, 1) if total else None,
        "avg_seconds": avg_sec,
        "avg_text": fmt_duration(avg_sec),
    }


def get_self_score_stats(db: DBSession) -> dict:
    """Самооценка ученика (student_score) против оценки куратора (score)."""
    rows = (
        db.query(Work.student_score, Work.score)
        .filter(Work.student_score.isnot(None), Work.score.isnot(None))
        .all()
    )
    if not rows:
        return {"count": 0, "avg_student": None, "avg_curator": None,
                "avg_diff": None, "student_higher_pct": None}
    students = [float(s) for s, _ in rows]
    curators = [float(c) for _, c in rows]
    diffs = [s - c for s, c in zip(students, curators)]
    higher = sum(1 for d in diffs if d > 0)
    return {
        "count": len(rows),
        "avg_student": round(sum(students) / len(students), 1),
        "avg_curator": round(sum(curators) / len(curators), 1),
        "avg_diff": round(sum(diffs) / len(diffs), 1),
        "student_higher_pct": round(100 * higher / len(rows), 1),
    }


_AUDIT_LABELS = {
    "curator_assign": "Смена куратора",
    "tariff_change": "Смена тарифа",
    "user_delete": "Удаление",
    "user_block": "Блокировка",
    "user_unblock": "Разблокировка",
}


def get_diagnostic_stats(db: DBSession) -> list[dict]:
    """Прохождение диагностик АРХИ-ПРОФИЛЯ: по каждой опубликованной
    диагностике — кто не начал/начал/закончил, за сколько времени, с каким
    результатом (владелец 24.09.2026: статистика диагностики переехала сюда,
    со своей отдельной страницы — вся статистика формируется на этой
    странице, см. инвариант в AGENTS.md).

    Диагностика бывает двух видов: отдельная задача (`kind=archi_profile`)
    и блоки внутри обычного «Задания» (`TaskBlock.is_diagnostic=True`,
    владелец 24.09.2026) — оба попадают в список одинаково. Подсчёт по
    каждой — `archi_profile_stats.diagnostic_stats`, уже используется и
    покрыт тестами, здесь только сбор списка диагностик.
    """
    from app.models.task_block import TaskBlock
    from app.models.tracker import ITEM_ARCHI_PROFILE, TrackerTask
    from app.services.archi_profile_stats import diagnostic_stats

    embedded_task_ids = (
        db.query(TaskBlock.task_id)
        .filter(TaskBlock.is_diagnostic.is_(True))
        .distinct()
        .scalar_subquery()
    )
    tasks = (
        db.query(TrackerTask)
        .filter(
            TrackerTask.deleted_at.is_(None),
            TrackerTask.is_published.is_(True),
            or_(
                TrackerTask.kind == ITEM_ARCHI_PROFILE,
                TrackerTask.id.in_(embedded_task_ids),
            ),
        )
        .order_by(TrackerTask.created_at.desc())
        .all()
    )
    return [diagnostic_stats(db, task) for task in tasks]


_EVENT_LABELS = {
    "login": "Входы в кабинет",
    "portfolio_upload": "Загрузки портфолио",
    "work_upload": "Загрузки работ",
}


def _names_by_id(db: DBSession, ids) -> dict[int, str]:
    ids = set(ids)
    if not ids:
        return {}
    return {
        uid: _student_name(fn, ln, nm)
        for uid, fn, ln, nm in (
            db.query(User.id, User.first_name, User.last_name, User.name)
            .filter(User.id.in_(ids))
            .all()
        )
    }


def get_student_event_stats(db: DBSession, days: int = RECENT_DAYS) -> dict:
    """Журнал `StudentActivityEvent` за последние `days` дней: сколько раз и
    сколько разных учеников входили и загружали работы, плюс самые активные.

    Журнал пишется давно (`auth.py`, `upload.py`), но до 25.09.2026 страница
    его не читала — видно было только время последнего входа.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    student_ids = _student_ids(db)
    base = (
        StudentActivityEvent.user_id.in_(student_ids),
        StudentActivityEvent.created_at >= since,
    )
    by_type = [
        {
            "event_type": et,
            "label": _EVENT_LABELS.get(et, et),
            "events": events,
            "students": students,
        }
        for et, events, students in (
            db.query(
                StudentActivityEvent.event_type,
                func.count(StudentActivityEvent.id),
                func.count(func.distinct(StudentActivityEvent.user_id)),
            )
            .filter(*base)
            .group_by(StudentActivityEvent.event_type)
            .all()
        )
    ]
    by_type.sort(key=lambda r: r["events"], reverse=True)

    per_user: dict[int, dict] = defaultdict(lambda: {"logins": 0, "uploads": 0, "last_at": None})
    for uid, et, n, last_at in (
        db.query(
            StudentActivityEvent.user_id,
            StudentActivityEvent.event_type,
            func.count(StudentActivityEvent.id),
            func.max(StudentActivityEvent.created_at),
        )
        .filter(*base)
        .group_by(StudentActivityEvent.user_id, StudentActivityEvent.event_type)
        .all()
    ):
        agg = per_user[uid]
        agg["logins" if et == "login" else "uploads"] += n
        last_at = _utc(last_at)
        if agg["last_at"] is None or last_at > agg["last_at"]:
            agg["last_at"] = last_at
    top_ids = sorted(
        per_user, key=lambda u: per_user[u]["logins"] + per_user[u]["uploads"], reverse=True,
    )[:15]
    names = _names_by_id(db, top_ids)
    top = [
        {
            "student_name": names.get(uid, f"id={uid}"),
            "logins": per_user[uid]["logins"],
            "uploads": per_user[uid]["uploads"],
            "last_at": _msk(per_user[uid]["last_at"]),
        }
        for uid in top_ids
    ]
    return {
        "days": days,
        "by_type": by_type,
        "active_students": len(per_user),
        "top": top,
    }


def get_video_watch_stats(db: DBSession, days: int = RECENT_DAYS) -> dict:
    """Просмотр видео учениками: кто начал, кто досмотрел, какая доля ролика
    реально просмотрена, сколько раз открывали плеер за `days` дней.

    `VideoProgress` — одна строка на пару ученик×ролик (позиция и накопленное
    время), `VideoViewLog` — каждое открытие плеера. Ролик адресуется
    bunny-id, а не FK: у легаси-роликов строки в каталоге нет, для них
    запасная подпись.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    student_ids = _student_ids(db)
    rows = (
        db.query(
            VideoProgress.user_id,
            VideoProgress.video_id,
            VideoProgress.watched_seconds,
            VideoProgress.duration_seconds,
            VideoProgress.completed_at,
        )
        .filter(VideoProgress.user_id.in_(student_ids))
        .all()
    )
    opens = dict(
        db.query(VideoViewLog.video_id, func.count(VideoViewLog.id))
        .filter(VideoViewLog.user_id.in_(student_ids), VideoViewLog.opened_at >= since)
        .group_by(VideoViewLog.video_id)
        .all()
    )

    def _share(r) -> float | None:
        # watched_seconds копит реальное время и при пересмотре перерастает
        # длину ролика — доля не выше 100%.
        if not r.duration_seconds:
            return None
        return min(float(r.watched_seconds or 0) / float(r.duration_seconds), 1.0)

    by_video: dict[str, dict] = {}

    def _video(vid: str) -> dict:
        return by_video.setdefault(vid, {"viewers": 0, "completed": 0, "shares": []})

    for r in rows:
        agg = _video(r.video_id)
        agg["viewers"] += 1
        if r.completed_at is not None:
            agg["completed"] += 1
        share = _share(r)
        if share is not None:
            agg["shares"].append(share)
    for vid in opens:
        _video(vid)  # плеер открывали, но позиция ещё не сохранялась

    titles = {}
    if by_video:
        titles = dict(
            db.query(LearningVideo.bunny_video_id, LearningVideo.title)
            .filter(LearningVideo.bunny_video_id.in_(list(by_video)))
            .all()
        )
    videos = []
    for vid, agg in by_video.items():
        shares = agg["shares"]
        videos.append({
            "title": titles.get(vid) or "Ролик вне каталога",
            "viewers": agg["viewers"],
            "completed": agg["completed"],
            "avg_share_pct": round(100 * sum(shares) / len(shares)) if shares else None,
            "opens": opens.get(vid, 0),
        })
    videos.sort(key=lambda v: (v["viewers"], v["opens"]), reverse=True)

    all_shares = [s for s in (_share(r) for r in rows) if s is not None]
    return {
        "days": days,
        "students_watching": len({r.user_id for r in rows}),
        "completed_count": sum(1 for r in rows if r.completed_at is not None),
        "started_count": len(rows),
        "avg_share_pct": round(100 * sum(all_shares) / len(all_shares)) if all_shares else None,
        "opens_total": sum(opens.values()),
        "videos": videos[:30],
    }


def get_task_progress_stats(db: DBSession) -> dict:
    """Задания учебной программы: сколько учеников открыли каждое, сколько
    закрыли, сколько бросили на середине и кто закрыл — сам ученик, система
    по событию или преподаватель.

    «Кто закрыл» читается по `completed_by_id`, а не по строкам источника:
    `None` — система (`tracker.complete_task_by_event`), id ученика — его
    галочка, любой другой id — преподаватель.
    """
    rows = (
        db.query(
            TrackerTaskState.task_id,
            TrackerTaskState.user_id,
            TrackerTaskState.started_at,
            TrackerTaskState.completed_at,
            TrackerTaskState.completed_by_id,
            TrackerTask.title,
        )
        .join(TrackerTask, TrackerTaskState.task_id == TrackerTask.id)
        .filter(
            TrackerTask.deleted_at.is_(None),
            TrackerTaskState.user_id.in_(_student_ids(db)),
        )
        .all()
    )
    closed_by = {"student": 0, "system": 0, "staff": 0}
    by_task: dict[int, dict] = {}
    for r in rows:
        agg = by_task.setdefault(r.task_id, {
            "title": r.title, "opened": 0, "done": 0, "stuck": 0, "pairs": [],
        })
        if r.started_at is not None:
            agg["opened"] += 1
        if r.completed_at is not None:
            agg["done"] += 1
            if r.started_at is not None:
                agg["pairs"].append((r.started_at, r.completed_at))
            if r.completed_by_id is None:
                closed_by["system"] += 1
            elif r.completed_by_id == r.user_id:
                closed_by["student"] += 1
            else:
                closed_by["staff"] += 1
        elif r.started_at is not None:
            agg["stuck"] += 1

    tasks = []
    for agg in by_task.values():
        agg["avg_text"] = fmt_duration(_avg_seconds(agg.pop("pairs")))
        tasks.append(agg)
    # Наверху — где больше всего начали и не закончили: это то, что требует
    # внимания преподавателя.
    tasks.sort(key=lambda t: (t["stuck"], t["opened"]), reverse=True)
    return {
        "opened": sum(t["opened"] for t in tasks),
        "done": sum(t["done"] for t in tasks),
        "stuck": sum(t["stuck"] for t in tasks),
        "closed_by": closed_by,
        "tasks": tasks[:30],
        "tasks_total": len(tasks),
    }


def get_submission_stats(db: DBSession) -> dict:
    """Сдачи внутри заданий: блоки «Домашнее задание»/«Работа на время»
    (`TaskBlockSubmission`) и самостоятельная работа (`HomeworkSubmission`).
    Для блоков — сколько ждут проверки и среднее время до первой реакции
    преподавателя (просмотр или балл, что раньше)."""
    student_ids = _student_ids(db)
    rows = (
        db.query(
            TaskBlockSubmission.submitted_at,
            TaskBlockSubmission.reviewed_at,
            TaskBlockSubmission.scored_at,
            TaskBlockSubmission.needs_revision,
        )
        .filter(
            TaskBlockSubmission.user_id.in_(student_ids),
            TaskBlockSubmission.submitted_at.isnot(None),
        )
        .all()
    )
    pairs = []
    waiting = 0
    for r in rows:
        reacted = [t for t in (_utc(r.reviewed_at), _utc(r.scored_at)) if t is not None]
        if reacted:
            pairs.append((r.submitted_at, min(reacted)))
        elif not r.needs_revision:
            waiting += 1

    hw_counts = dict(
        db.query(HomeworkSubmission.status, func.count(HomeworkSubmission.id))
        .filter(HomeworkSubmission.user_id.in_(student_ids))
        .group_by(HomeworkSubmission.status)
        .all()
    )
    return {
        "blocks_total": len(rows),
        "blocks_waiting": waiting,
        "blocks_scored": sum(1 for r in rows if r.scored_at is not None),
        "blocks_revision": sum(1 for r in rows if r.needs_revision),
        "avg_reaction_text": fmt_duration(_avg_seconds(pairs)),
        "homework": {status: hw_counts.get(status, 0) for status in SUBMISSION_STATUSES},
        "homework_total": sum(hw_counts.values()),
    }


def get_staff_activity(db: DBSession, days: int = RECENT_DAYS) -> list[dict]:
    """Действия сотрудников (ранг ≥ 2) по одной строке на человека.

    Каждый источник — один сгруппированный запрос по id сотрудника, склейка
    в Python. Проверки (`scored_by_id`, `reviewed_by_id`) хранят только
    последнюю попытку: пересдача их обнуляет, поэтому это «по последней
    попытке», а не полная история. Входы и записи аудита — за `days` дней.
    """
    staff = (
        db.query(
            User.id, User.first_name, User.last_name, User.name,
            User.last_login_at, Role.rank, Role.display_name,
        )
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank >= 2, User.is_active == True, User.deleted_at.is_(None))  # noqa: E712
        .all()
    )
    if not staff:
        return []
    ids = [s.id for s in staff]
    since = datetime.now(timezone.utc) - timedelta(days=days)

    def _count(col, *filters) -> dict[int, int]:
        return dict(
            db.query(col, func.count())
            .filter(col.in_(ids), *filters)
            .group_by(col)
            .all()
        )

    logins = _count(
        StudentActivityEvent.user_id,
        StudentActivityEvent.event_type == "login",
        StudentActivityEvent.created_at >= since,
    )
    works = _count(Work.scored_by_id)
    # Сдачу могут посмотреть и оценить разные люди (куратор открыл, ГП
    # поставил балл) — зачёт обоим, а своя двойная отметка считается один раз.
    blocks_reviewed = _count(TaskBlockSubmission.reviewed_by_id)
    blocks_scored = _count(TaskBlockSubmission.scored_by_id)
    blocks_both = _count(
        TaskBlockSubmission.reviewed_by_id,
        TaskBlockSubmission.scored_by_id == TaskBlockSubmission.reviewed_by_id,
    )
    answers = _count(TaskBlockAnswer.reviewed_by_id)
    messages: dict[int, int] = defaultdict(int)
    for model in (FeedbackMessage, HomeworkFeedbackMessage, TaskBlockFeedbackMessage):
        for uid, n in _count(model.sender_id, model.sender_role != ROLE_STUDENT).items():
            messages[uid] += n
    actions = _count(AuditLog.performed_by_id, AuditLog.created_at >= since)
    reports = _count(CuratorReport.curator_id)

    result = [
        {
            "user_id": s.id,
            "name": _student_name(s.first_name, s.last_name, s.name),
            "role_label": s.display_name,
            "role_group": role_group(s.rank),
            "last_login_at": _msk(s.last_login_at),
            "logins": logins.get(s.id, 0),
            "works_scored": works.get(s.id, 0),
            "blocks_checked": (
                blocks_reviewed.get(s.id, 0) + blocks_scored.get(s.id, 0) - blocks_both.get(s.id, 0)
            ),
            "answers_reviewed": answers.get(s.id, 0),
            "messages": messages.get(s.id, 0),
            "actions": actions.get(s.id, 0),
            "reports": reports.get(s.id, 0),
        }
        for s in staff
    ]
    # Сначала те, кто заходил недавно; не заходившие с 11.07 — в конце.
    result.sort(
        key=lambda r: r["last_login_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return result


def get_audit_feed(db: DBSession, limit: int = 50) -> list[dict]:
    """Последние записи аудита (смены куратора/тарифа + admin-действия)."""
    rows = (
        db.query(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    need_ids = {r.performed_by_id for r in rows} | {
        r.target_user_id for r in rows if r.target_user_id
    }
    names: dict[int, str] = {}
    if need_ids:
        for uid, fn, ln, nm in (
            db.query(User.id, User.first_name, User.last_name, User.name)
            .filter(User.id.in_(need_ids))
            .all()
        ):
            names[uid] = _student_name(fn, ln, nm)
    return [
        {
            "created_at": _msk(r.created_at),
            "action": r.action,
            "action_label": _AUDIT_LABELS.get(r.action, r.action),
            "performed_by": names.get(r.performed_by_id, f"id={r.performed_by_id}"),
            "target": names.get(r.target_user_id) if r.target_user_id else None,
            "details": r.details,
        }
        for r in rows
    ]
