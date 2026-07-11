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

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app.models.audit_log import AuditLog
from app.models.curator_report import CuratorReport
from app.models.exam_cycle import ExamCycle
from app.models.notification import Notification
from app.models.role import Role
from app.models.user import User
from app.models.work import Work
from app.services.tz import MSK_TZ

# Дата деплоя миграций — раньше неё новых таймстемпов не существует
ACTIVITY_STATS_START = datetime(2026, 7, 11, tzinfo=timezone.utc)


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
            "scored_count": len(items),
            "avg_review_seconds": avg_sec,
            "avg_review_text": fmt_duration(avg_sec),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        })
    result.sort(key=lambda r: r["scored_count"], reverse=True)
    return result


def get_notification_reaction(db: DBSession) -> dict:
    """Время реакции на уведомления (read_at копится с 11.07.2026)."""
    read_pairs = (
        db.query(Notification.created_at, Notification.read_at)
        .filter(Notification.read_at.isnot(None))
        .all()
    )
    unread_total = (
        db.query(Notification)
        .filter(Notification.is_read == False)  # noqa: E712
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
    needs_setup = sum(1 for s in students if not s.course_periods or not s.lessons_count)

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


_AUDIT_LABELS = {
    "curator_assign": "Смена куратора",
    "tariff_change": "Смена тарифа",
    "user_delete": "Удаление",
    "user_block": "Блокировка",
    "user_unblock": "Разблокировка",
}


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
