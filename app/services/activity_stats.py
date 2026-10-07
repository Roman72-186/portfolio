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
from app.models.feedback_rating import FEEDBACK_TYPE_LABELS, FeedbackRating
from app.models.homework_feedback import HomeworkFeedbackMessage
from app.models.homework_submission import HomeworkSubmission, SUBMISSION_STATUSES
from app.models.learning_topic import LearningTopic
from app.models.learning_video import LearningVideo
from app.models.login_token import LoginToken
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.notification import Notification
from app.models.role import Role
from app.models.task_block import TaskBlockAnswer, TaskBlockState, TaskBlockSubmission
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.models.tracker import TrackerTask, TrackerTaskState
from app.models.user import User
from app.models.video_progress import VideoProgress
from app.models.video_view_log import VideoViewLog
from app.models.video_watch_event import VideoWatchEvent
from app.models.work import Work, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE
from app.services.feedback import ROLE_STUDENT
from app.services.program import day_bounds, week_start
from app.services.report_scope import reportable_students_q
from app.services.tracker import accessible_task_entries, effective_week_start
from app.services.tz import MSK_TZ, msk_midnight
from app.services.video_progress import watch_threshold_seconds
from app.services.video_watch_events import (
    CUT_LABELS,
    CUT_NETWORK,
    CUT_PAUSED_PLAYING,
    CUT_SEEK,
    HARMLESS_CUTS,
    KIND_CUT,
    KIND_REFUSAL,
    refusal_text,
)

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
    """Ученики в учёте — правило одно на всю статистику (`report_scope`)."""
    return reportable_students_q(db)


def _student_ids(db: DBSession):
    """Подзапрос id активных учеников для фильтра `col.in_(...)`.

    Входы, просмотры видео, открытие заданий и уведомления пишутся и у
    сотрудников — без этого фильтра ученические цифры молча смешались бы
    с кураторскими.
    """
    return _active_students_q(db).with_entities(User.id).scalar_subquery()


def _scope_ids(db: DBSession, student_id: int | None):
    """Чьи строки считать: все активные ученики школы (страница «Статистика
    активности») или один ученик — вкладка «Статистика» его карточки
    (владелец 06.10.2026: «собрать туда всё, что касается ребёнка»).

    Один ученик берётся как есть, без фильтров `_active_students_q`: карточку
    открывают и у архивного, и тогда его прошлые цифры должны быть видны.
    """
    return [student_id] if student_id is not None else _student_ids(db)


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
        .filter(
            Work.scored_at.isnot(None),
            Work.scored_by_id.isnot(None),
            Work.user_id.in_(_student_ids(db)),
        )
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
            ExamCycle.user_id.in_(_student_ids(db)),
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
        .filter(
            ExamCycle.revision_done_at.isnot(None),
            ExamCycle.user_id.in_(_student_ids(db)),
        )
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
    ).filter(MockExamAttempt.user_id.in_(_student_ids(db))).all()
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
    rows = (
        db.query(ExamCycle.started_at, ExamCycle.closed_at)
        .filter(ExamCycle.user_id.in_(_student_ids(db)))
        .all()
    )
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


def _staff_names(db: DBSession, ids) -> dict[int, tuple[str, int]]:
    """Имя и ранг сотрудников по id — подпись строки и вкладка роли."""
    if not ids:
        return {}
    return {
        uid: (_student_name(fn, ln, nm), rank or 0)
        for uid, fn, ln, nm, rank in (
            db.query(User.id, User.first_name, User.last_name, User.name, Role.rank)
            .outerjoin(Role, User.role_id == Role.id)
            .filter(User.id.in_(list(ids)))
            .all()
        )
    }


def get_feedback_curator_stats(db: DBSession) -> list[dict]:
    """ОС по авторам: сколько раз дал ОС, сообщений на диалог, время до первой
    ОС и средняя оценка ОС учениками (ОС, фаза 2; владелец 01.10.2026, О19, О21).

    Диалогов два вида: пробник (`Feedback`, отсчёт от загрузки финальной
    работы) и сдача в блоке задания (`TaskBlockFeedback`, от `submitted_at`).
    «Время до первой ОС» — до первого сообщения сотрудника: балл с 30.09.2026
    ставит только ГП, и прежняя «скорость проверки» у кураторов перестала
    пополняться. Диалог заводит первое сообщение сотрудника, поэтому «дал ОС»
    — это число диалогов автора. Средняя оценка — по тому же автору
    (`FeedbackRating.curator_id` = `curator_id` диалога), кто бы ни нажал
    «Завершить ОС» (владелец 05.10.2026).
    """
    dialogs: list[tuple[str, int, int, datetime | None]] = []
    for fb_id, curator_id, started in (
        db.query(Feedback.id, Feedback.curator_id, Work.created_at)
        .join(Work, Feedback.work_id == Work.id)
        .filter(Work.user_id.in_(_student_ids(db)))
        .all()
    ):
        dialogs.append(("mock", fb_id, curator_id, started))
    for fb_id, curator_id, started in (
        db.query(TaskBlockFeedback.id, TaskBlockFeedback.curator_id, TaskBlockSubmission.submitted_at)
        .join(TaskBlockSubmission, TaskBlockFeedback.submission_id == TaskBlockSubmission.id)
        .filter(TaskBlockSubmission.user_id.in_(_student_ids(db)))
        .all()
    ):
        dialogs.append(("block", fb_id, curator_id, started))

    msgs: dict[tuple[str, int], list] = defaultdict(list)
    for kind, model in (("mock", FeedbackMessage), ("block", TaskBlockFeedbackMessage)):
        for m in (
            db.query(model.feedback_id, model.sender_role, model.created_at)
            .order_by(model.created_at, model.id)
            .all()
        ):
            msgs[(kind, m.feedback_id)].append(m)

    by_curator: dict[int, dict] = defaultdict(
        lambda: {"dialogs": 0, "messages": 0, "first_pairs": [], "scores": []}
    )
    for kind, fb_id, curator_id, started in dialogs:
        agg = by_curator[curator_id]
        agg["dialogs"] += 1
        dialog_msgs = msgs.get((kind, fb_id), [])
        agg["messages"] += len(dialog_msgs)
        first_staff = next((m for m in dialog_msgs if m.sender_role != ROLE_STUDENT), None)
        if first_staff is not None:
            agg["first_pairs"].append((started, first_staff.created_at))
    for curator_id, score in (
        db.query(FeedbackRating.curator_id, FeedbackRating.score)
        .filter(
            FeedbackRating.curator_id.isnot(None),
            FeedbackRating.student_id.in_(_student_ids(db)),
        )
        .all()
    ):
        by_curator[curator_id]["scores"].append(score)
    if not by_curator:
        return []

    names = _staff_names(db, by_curator.keys())
    result = []
    for cid, agg in by_curator.items():
        first_sec = _avg_seconds(agg["first_pairs"])
        name, rank = names.get(cid, (f"id={cid}", 0))
        scores = agg["scores"]
        result.append({
            "curator_id": cid,
            "curator_name": name,
            "role_rank": rank,
            "role_group": role_group(rank),
            "dialogs": agg["dialogs"],
            "avg_messages": round(agg["messages"] / agg["dialogs"], 1) if agg["dialogs"] else None,
            "avg_first_response_seconds": first_sec,
            "avg_first_response_text": fmt_duration(first_sec),
            "ratings": len(scores),
            "avg_rating": round(sum(scores) / len(scores), 1) if scores else None,
        })
    result.sort(key=lambda r: r["dialogs"], reverse=True)
    return result


def get_feedback_rating_by_task(db: DBSession) -> list[dict]:
    """Средняя оценка ОС куратора по каждому заданию цикла (О19): «ОС по
    домашке недели 3 октября» — отдельная строка с числом оценок. Пробник —
    строкой по предмету. Подпись задания хранится в оценке на момент оценки,
    неделя или цикл подтягивается по живому заданию."""
    rows = (
        db.query(
            FeedbackRating.curator_id, FeedbackRating.feedback_type,
            FeedbackRating.task_id, FeedbackRating.task_title, FeedbackRating.score,
            FeedbackRating.created_at,
        )
        .filter(
            FeedbackRating.curator_id.isnot(None),
            FeedbackRating.student_id.in_(_student_ids(db)),
        )
        .all()
    )
    if not rows:
        return []
    task_ids = {r.task_id for r in rows if r.task_id is not None}
    topics: dict[int, str] = {}
    if task_ids:
        topics = {
            task_id: title
            for task_id, title in (
                db.query(TrackerTask.id, LearningTopic.title)
                .join(LearningTopic, TrackerTask.topic_id == LearningTopic.id)
                .filter(TrackerTask.id.in_(task_ids))
                .all()
            )
        }
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r.curator_id, r.feedback_type, r.task_id if r.task_id is not None else r.task_title)
        group = groups.setdefault(key, {
            "curator_id": r.curator_id, "feedback_type": r.feedback_type,
            "task_title": r.task_title, "topic_title": topics.get(r.task_id),
            "scores": [], "last_at": None,
        })
        group["scores"].append(r.score)
        created = _utc(r.created_at)
        if group["last_at"] is None or (created and created > group["last_at"]):
            group["last_at"] = created

    names = _staff_names(db, {g["curator_id"] for g in groups.values()})
    result = []
    for group in groups.values():
        name, rank = names.get(group["curator_id"], (f"id={group['curator_id']}", 0))
        scores = group["scores"]
        result.append({
            "curator_name": name,
            "role_group": role_group(rank),
            "feedback_label": FEEDBACK_TYPE_LABELS.get(group["feedback_type"], group["feedback_type"]),
            "task_title": group["task_title"] or "—",
            "topic_title": group["topic_title"],
            "ratings": len(scores),
            "avg_rating": round(sum(scores) / len(scores), 1),
            "last_at": group["last_at"],
        })
    # Свежие задания сверху: смотрят обычно последнюю волну ОС.
    result.sort(
        key=lambda r: r["last_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True,
    )
    return result


def get_retake_stats(db: DBSession) -> dict:
    """Пересдачи и доработки: доля отправленных на отработку/доработку,
    среднее число попыток в цикле."""
    reportable = Work.user_id.in_(_student_ids(db))
    mock_total = (
        db.query(Work)
        .filter(Work.work_type == WORK_TYPE_MOCK_EXAM, Work.status == "success", reportable)
        .count()
    )
    retake_total = (
        db.query(Work)
        .filter(Work.work_type == WORK_TYPE_RETAKE, Work.status == "success", reportable)
        .count()
    )
    sent_to_retake = (
        db.query(Work)
        .filter(Work.work_type == WORK_TYPE_MOCK_EXAM, Work.sent_to_retake == True, reportable)  # noqa: E712
        .count()
    )
    needs_revision = (
        db.query(Work)
        .filter(Work.needs_revision == True, reportable)  # noqa: E712
        .count()
    )
    attempt_numbers = [
        n for (n,) in db.query(Work.attempt_number)
        .filter(Work.is_final == True, Work.attempt_number.isnot(None), reportable)  # noqa: E712
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
    rows = (
        db.query(LoginToken.created_at, LoginToken.used_at, LoginToken.revoked_at)
        .filter(LoginToken.user_id.in_(_student_ids(db)))
        .all()
    )
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
        .filter(
            Work.student_score.isnot(None),
            Work.score.isnot(None),
            Work.user_id.in_(_student_ids(db)),
        )
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
    "user_rename": "Смена имени",
    "user_delete": "Удаление",
    "user_block": "Блокировка",
    "user_unblock": "Разблокировка",
    # Действия над учеником — лента в его карточке (`student_activity`).
    "access_until_change": "Смена срока доступа",
    "payment_settings_change": "Настройки оплаты",
    "user_archive": "В архив",
    "user_unarchive": "Из архива",
    "impersonate_start": "Вход в кабинет ученика",
    "impersonate_stop": "Выход из кабинета ученика",
    # Дайджест месяца (`api/cabinet_digest_admin.py`): сам дайджест, его
    # события и типы событий. Без подписи строка показывала бы ключ.
    "digest_create": "Дайджест: создан",
    "digest_update": "Дайджест: изменён",
    "digest_publish": "Дайджест: опубликован",
    "digest_unpublish": "Дайджест: скрыт",
    "digest_delete": "Дайджест: удалён",
    "digest_event_create": "Событие дайджеста: создано",
    "digest_event_update": "Событие дайджеста: изменено",
    "digest_event_delete": "Событие дайджеста: удалено",
    "event_type_create": "Тип события: создан",
    "event_type_update": "Тип события: изменён",
    "event_type_archive": "Тип события: скрыт",
    "event_type_restore": "Тип события: возвращён",
    "event_type_delete": "Тип события: удалён",
}


def get_diagnostic_stats(db: DBSession, *, student_id: int | None = None) -> list[dict]:
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

    `student_id` — карточка ученика: строка только его, диагностики, которые
    ему не адресованы (`total == 0`), не отдаются.
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
    if student_id is None:
        return [diagnostic_stats(db, task) for task in tasks]
    stats = (diagnostic_stats(db, task, only_user_id=student_id) for task in tasks)
    return [item for item in stats if item["total"]]


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


def get_video_watch_stats(
    db: DBSession, days: int = RECENT_DAYS, *, student_id: int | None = None,
) -> dict:
    """Просмотр видео учениками: кто начал, кто досмотрел, какая доля ролика
    реально просмотрена, сколько раз открывали плеер за `days` дней.

    `VideoProgress` — одна строка на пару ученик×ролик (позиция и накопленное
    время), `VideoViewLog` — каждое открытие плеера. Ролик адресуется
    bunny-id, а не FK: у легаси-роликов строки в каталоге нет, для них
    запасная подпись.

    `student_id` — один ученик (карточка): «смотрели» по ролику тогда 0 или 1,
    доля — его собственная.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    student_ids = _scope_ids(db, student_id)
    rows = (
        db.query(
            VideoProgress.user_id,
            VideoProgress.video_id,
            VideoProgress.watched_seconds,
            VideoProgress.covered_seconds,
            VideoProgress.duration_seconds,
            VideoProgress.completed_at,
            VideoProgress.last_completed_at,
            VideoProgress.updated_at,
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
        # Доля — сколько разных секунд ролика просмотрено (покрытие прохода,
        # 07.10.2026), а не сумма: пересмотр начала долю не растит. Засчитанный
        # ролик — целиком: после зачёта покрытие копит уже новый проход.
        if not r.duration_seconds:
            return None
        if r.completed_at is not None:
            return 1.0
        return min(float(r.covered_seconds or 0) / float(r.duration_seconds), 1.0)

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
    # Один ученик (карточка): почему не засчитано — по каждому ролику
    # (владелец 07.10.2026). На странице школы этих полей нет, там своя
    # карточка `get_video_loss_stats`.
    details = _video_event_details(db, student_id) if student_id is not None else {}
    for vid in details:
        _video(vid)  # кружок отказал, а ролик не запускался
    own = {r.video_id: r for r in rows} if student_id is not None else {}

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
        item = {
            "title": titles.get(vid) or "Ролик вне каталога",
            "viewers": agg["viewers"],
            "completed": agg["completed"],
            "avg_share_pct": round(100 * sum(shares) / len(shares)) if shares else None,
            "opens": opens.get(vid, 0),
        }
        if student_id is not None:
            item.update(_own_watch(own.get(vid)))
            item.update(details.get(vid) or _empty_video_details())
        videos.append(item)
    if student_id is not None:
        # У одного ученика — свежие сверху: жалоба обычно про вчерашний ролик.
        videos.sort(key=lambda v: v.pop("sort_at"), reverse=True)
    else:
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


# Сколько последних событий незачёта ученика читать для хронологии карточки
# и сколько показывать под одним роликом. Число запросов от объёма истории не
# растёт: выборки ограничены, агрегат — один GROUP BY.
VIDEO_EVENT_HISTORY_LIMIT = 80
VIDEO_EVENT_HISTORY_PER_VIDEO = 8
VIDEO_REFUSAL_SCAN_LIMIT = 200
_NEVER = datetime.min.replace(tzinfo=timezone.utc)


def _when_msk(value: datetime | None) -> str | None:
    value = _msk(value)
    return value.strftime("%d.%m.%Y %H:%M") if value else None


def _mmss(seconds: float | None) -> str:
    seconds = int(round(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _empty_video_details() -> dict:
    return {
        "lost_seconds": 0,
        "losses": [],
        "harmless_seconds": 0,
        "refusals": 0,
        "last_refusal": None,
        "history": [],
    }


def _own_watch(row) -> dict:
    """Засчитано и нужно по ролику одного ученика — по его `VideoProgress`."""
    if row is None:
        return {
            "watch_state": "not_started", "credited_seconds": 0, "needed_seconds": None,
            "completed_at": None, "last_watch": None, "sort_at": _NEVER,
        }
    completed_at = row.last_completed_at or row.completed_at
    needed = watch_threshold_seconds(row.duration_seconds) if row.duration_seconds else None
    return {
        # У засчитанного ролика страница пишет дату зачёта, а не «засчитано
        # 0:30 из 9:30» нового прохода — то читалось бы как незачёт.
        "watch_state": "completed" if completed_at else "watching",
        "credited_seconds": round(float(row.covered_seconds or 0)),
        "needed_seconds": round(needed) if needed is not None else None,
        "completed_at": _when_msk(completed_at),
        "last_watch": _when_msk(row.updated_at),
        "sort_at": _utc(row.updated_at) or _NEVER,
    }


def _video_event_text(event: VideoWatchEvent) -> str:
    if event.kind == KIND_REFUSAL:
        return "Кружок не поставлен: " + refusal_text(event)
    text = (
        f"{CUT_LABELS.get(event.reason, event.reason)}: {_mmss(event.position_from)}"
        f" → {_mmss(event.position_to)}, не засчитано {_mmss(event.skipped_seconds)}"
    )
    if event.reason in HARMLESS_CUTS:
        text += " (уже было засчитано — не потеря)"
    return text


def _video_event_details(db: DBSession, student_id: int) -> dict[str, dict]:
    """Срезы и отказы кружка одного ученика по роликам (`VideoWatchEvent`).

    Потери — только настоящие, `HARMLESS_CUTS` отдельно: скачок внутрь уже
    засчитанного ничего не отнимает, а по секундам это больше половины всех
    срезов (прод 06.10.2026)."""
    details: dict[str, dict] = {}
    losses: dict[str, dict[str, list]] = defaultdict(dict)

    def _d(vid: str) -> dict:
        return details.setdefault(vid, _empty_video_details())

    agg = (
        db.query(
            VideoWatchEvent.video_id,
            VideoWatchEvent.kind,
            VideoWatchEvent.reason,
            func.count(VideoWatchEvent.id),
            func.coalesce(func.sum(VideoWatchEvent.skipped_seconds), 0.0),
        )
        .filter(VideoWatchEvent.user_id == student_id)
        .group_by(VideoWatchEvent.video_id, VideoWatchEvent.kind, VideoWatchEvent.reason)
        .all()
    )
    for vid, kind, reason, count, seconds in agg:
        d = _d(vid)
        seconds = float(seconds or 0)
        if kind == KIND_REFUSAL:
            d["refusals"] += count
        elif reason in HARMLESS_CUTS:
            d["harmless_seconds"] += seconds
        else:
            d["lost_seconds"] += seconds
            losses[vid][reason] = [count, seconds]

    refusals = (
        db.query(VideoWatchEvent)
        .filter(VideoWatchEvent.user_id == student_id, VideoWatchEvent.kind == KIND_REFUSAL)
        .order_by(VideoWatchEvent.created_at.desc(), VideoWatchEvent.id.desc())
        .limit(VIDEO_REFUSAL_SCAN_LIMIT)
        .all()
    )
    for e in refusals:
        d = _d(e.video_id)
        if d["last_refusal"] is None:
            d["last_refusal"] = {"text": refusal_text(e), "at": _when_msk(e.created_at)}

    recent = (
        db.query(VideoWatchEvent)
        .filter(VideoWatchEvent.user_id == student_id)
        .order_by(VideoWatchEvent.created_at.desc(), VideoWatchEvent.id.desc())
        .limit(VIDEO_EVENT_HISTORY_LIMIT)
        .all()
    )
    for e in recent:
        d = _d(e.video_id)
        if len(d["history"]) < VIDEO_EVENT_HISTORY_PER_VIDEO:
            d["history"].append({
                "at": _when_msk(e.created_at),
                "text": _video_event_text(e),
                "harmless": e.kind == KIND_CUT and e.reason in HARMLESS_CUTS,
                "refusal": e.kind == KIND_REFUSAL,
            })

    for vid, d in details.items():
        d["losses"] = [
            {"label": CUT_LABELS.get(reason, reason), "count": count, "seconds": round(seconds)}
            for reason, (count, seconds) in sorted(
                losses[vid].items(), key=lambda kv: kv[1][1], reverse=True,
            )
        ]
        d["lost_seconds"] = round(d["lost_seconds"])
        d["harmless_seconds"] = round(d["harmless_seconds"])
    return details


# Окно карточки «Незачёт видео» на «Статистике активности».
VIDEO_LOSS_DAYS = 7
VIDEO_LOSS_TOP = 15


def get_video_loss_stats(db: DBSession, days: int = VIDEO_LOSS_DAYS) -> dict:
    """Незачёт видео по школе за `days` дней (владелец 07.10.2026): сколько
    минут ролика срезано и почему, сколько раз отказал кружок, у кого больше
    всего.

    «Плеер считал паузой» — сигнал сбоя самого плеера: после починки
    07.10.2026 (`b30fe01`) колонка должна стоять в нуле. Скачки внутрь
    засчитанного (продолжение, второй плеер) потерей не считаются и идут
    отдельной колонкой.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(
            VideoWatchEvent.user_id,
            VideoWatchEvent.kind,
            VideoWatchEvent.reason,
            VideoWatchEvent.skipped_seconds,
            VideoWatchEvent.created_at,
        )
        .filter(
            VideoWatchEvent.user_id.in_(_student_ids(db)),
            VideoWatchEvent.created_at >= since,
        )
        .all()
    )

    def _blank() -> dict:
        return {
            "lost": 0.0, CUT_PAUSED_PLAYING: 0.0, CUT_SEEK: 0.0, CUT_NETWORK: 0.0,
            "harmless": 0.0, "refusals": 0,
        }

    totals = _blank()
    by_day: dict = defaultdict(_blank)
    by_user: dict[int, dict] = defaultdict(_blank)
    for r in rows:
        for bucket in (totals, by_day[_msk(r.created_at).date()], by_user[r.user_id]):
            if r.kind == KIND_REFUSAL:
                bucket["refusals"] += 1
                continue
            seconds = float(r.skipped_seconds or 0)
            if r.reason in HARMLESS_CUTS:
                bucket["harmless"] += seconds
                continue
            bucket["lost"] += seconds
            if r.reason in bucket:
                bucket[r.reason] += seconds

    def _minutes(bucket: dict) -> dict:
        # Секунды → минуты для показа; отказы — штуки, как есть.
        return {
            key: value if key == "refusals" else round(value / 60)
            for key, value in bucket.items()
        }

    top_ids = [
        uid for uid, b in sorted(
            by_user.items(), key=lambda kv: (kv[1]["lost"], kv[1]["refusals"]), reverse=True,
        )
        if b["lost"] >= 60 or b["refusals"]
    ][:VIDEO_LOSS_TOP]
    # Ник зашифрован (`EncryptedString`) — читается только через ORM.
    people = (
        {u.id: u for u in db.query(User).filter(User.id.in_(top_ids)).all()}
        if top_ids else {}
    )
    students = [
        {
            "id": uid,
            "name": _student_name(people[uid].first_name, people[uid].last_name, people[uid].name),
            "username": (
                f"@{people[uid].tg_username.strip().lstrip('@')}"
                if people[uid].tg_username else "Не указан"
            ),
            "url": f"/cabinet/students?student={uid}&tab=statistics",
            **_minutes(by_user[uid]),
        }
        for uid in top_ids
        if uid in people
    ]

    return {
        "days": days,
        "totals": _minutes(totals),
        "by_day": [
            {"day": day.strftime("%d.%m"), **_minutes(bucket)}
            for day, bucket in sorted(by_day.items(), reverse=True)
        ],
        "students": students,
    }


def get_task_progress_stats(db: DBSession) -> dict:
    """Задания учебной программы: сколько учеников открыли каждое, сколько
    закрыли, у скольких оно открыто и срок ещё идёт, у скольких срок прошёл,
    и кто закрыл — сам ученик, система по событию или преподаватель.

    «Просрочено» — только по `TrackerTask.due_at`: у заданий цикла срока нет
    (`due_at IS NULL`, окно задаёт период цикла), такие незакрытые остаются
    «в работе». Иначе ученик, открывший задание час назад, считался бы
    бросившим.

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
            TrackerTask.due_at,
        )
        .join(TrackerTask, TrackerTaskState.task_id == TrackerTask.id)
        .filter(
            TrackerTask.deleted_at.is_(None),
            TrackerTaskState.user_id.in_(_student_ids(db)),
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    closed_by = {"student": 0, "system": 0, "staff": 0}
    by_task: dict[int, dict] = {}
    for r in rows:
        agg = by_task.setdefault(r.task_id, {
            "title": r.title, "opened": 0, "done": 0, "in_work": 0, "overdue": 0,
            "pairs": [],
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
            due_at = _utc(r.due_at)
            agg["overdue" if due_at is not None and due_at < now else "in_work"] += 1

    tasks = []
    for agg in by_task.values():
        agg["avg_text"] = fmt_duration(_avg_seconds(agg.pop("pairs")))
        tasks.append(agg)
    # Наверху — где больше всего просрочено: это то, что требует внимания
    # преподавателя.
    tasks.sort(key=lambda t: (t["overdue"], t["in_work"], t["opened"]), reverse=True)
    return {
        "opened": sum(t["opened"] for t in tasks),
        "done": sum(t["done"] for t in tasks),
        "in_work": sum(t["in_work"] for t in tasks),
        "overdue": sum(t["overdue"] for t in tasks),
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


def get_deadline_stats(db: DBSession, *, student_id: int | None = None) -> dict:
    """Сдано до срока и после срока (владелец 27.09.2026: «записывать всё
    нужно в статистику, что сдано после дедлайна, до дедлайна»).

    Считается по `TaskBlockState.completed_at` — моменту, когда блок закрылся
    у ученика. Он есть у блока любого типа: и у сдачи работы, и у ответа на
    вопрос, и у кружка «Выполнено» у видео. Поэтому разрез один на все виды
    заданий, а не отдельная таблица под каждый.

    **Срок берётся текущий, а не тот, что стоял в момент сдачи.** Продлили
    дедлайн — прошлые опоздания перестают считаться опозданиями, и это
    сознательно: «продлили, значит успел». Хранить снимок срока у каждой
    сдачи значило бы держать вторую копию правила, которая молча разъедется
    с настоящим сроком при первой же правке (та же причина, по которой не
    хранится средний балл точки А).

    Если срока нет ни у блока, ни у задания, сроком считается срок цикла,
    к которому приписано задание (владелец 28.09.2026: видео отмечают и после
    конца этапа, «записывать всё для каждого ученика»): «по» тарифа ученика,
    а без неё конец цикла (`tracker.cycle_deadline_for`, 06.10.2026). Это
    срок только для отчёта: отметку он не запирает. Первую сдачу не
    запирает и срок блока — с 06.10.2026 после него принимают опозданием
    (`submission_edit.deadline_reason`). Явное «бессрочно» строкой тарифа
    остаётся бессрочным. Задание без цикла и без срока в подсчёт не входит:
    «вовремя» у него не определено.

    `student_id` — один ученик (карточка): `tasks` — его задания со сроком.
    """
    from app.models.task_block import TaskBlock
    from app.models.tracker import TrackerTask
    from app.services.task_blocks import (
        finished_after_deadline, get_submit_deadlines, get_task_submit_deadlines,
    )
    from app.services.tracker import cycle_deadline_lookup

    student_ids = _scope_ids(db, student_id)
    rows = (
        db.query(
            TaskBlockState.completed_at,
            TaskBlockState.user_id,
            TaskBlock,
            TrackerTask,
            User.name,
            User.tariff,
        )
        .join(TaskBlock, TaskBlock.id == TaskBlockState.block_id)
        .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
        .join(User, User.id == TaskBlockState.user_id)
        .filter(
            TaskBlockState.user_id.in_(student_ids),
            TaskBlockState.completed_at.isnot(None),
            TrackerTask.deleted_at.is_(None),
        )
        .all()
    )
    # Домашка — те же правила, что у блоков сдачи (владелец 28.09.2026): блока
    # у неё нет (`None`), срок берётся у задания, момент — `submitted_at`.
    rows = list(rows) + [
        (submitted_at, user_id, None, task, name, tariff)
        for submitted_at, user_id, task, name, tariff in db.query(
            HomeworkSubmission.submitted_at,
            HomeworkSubmission.user_id,
            TrackerTask,
            User.name,
            User.tariff,
        )
        .join(TrackerTask, TrackerTask.id == HomeworkSubmission.tracker_task_id)
        .join(User, User.id == HomeworkSubmission.user_id)
        .filter(
            HomeworkSubmission.user_id.in_(student_ids),
            HomeworkSubmission.submitted_at.isnot(None),
            TrackerTask.deleted_at.is_(None),
        )
        .all()
    ]
    block_deadlines = get_submit_deadlines(db, [r[2].id for r in rows if r[2] is not None])
    task_deadlines = get_task_submit_deadlines(db, [r[3].id for r in rows])
    cycle_deadline = cycle_deadline_lookup(db, {r[3].topic_id for r in rows})

    by_student: dict[int, dict] = {}
    by_task: dict[int, dict] = {}
    on_time = late = 0
    for completed_at, user_id, block, task, name, tariff in rows:
        # Правило одно с отметкой на проверке — `finished_after_deadline`;
        # ничего не настроено — сроком служит срок цикла.
        is_late = finished_after_deadline(
            completed_at, block, task, user_tariff=tariff,
            block_overrides=block_deadlines.get(block.id) if block else None,
            task_overrides=task_deadlines.get(task.id),
            cycle_deadline=cycle_deadline(task.topic_id, tariff),
        )
        if is_late is None:
            continue
        on_time += 0 if is_late else 1
        late += 1 if is_late else 0
        student = by_student.setdefault(
            user_id, {"name": name, "tariff": tariff, "on_time": 0, "late": 0}
        )
        student["late" if is_late else "on_time"] += 1
        item = by_task.setdefault(
            task.id, {"title": task.title, "on_time": 0, "late": 0}
        )
        item["late" if is_late else "on_time"] += 1

    def _ranked(rows_map: dict[int, dict]) -> list[dict]:
        # Сверху те, у кого опозданий больше: с них и начинают разбираться.
        return sorted(
            rows_map.values(),
            key=lambda row: (-row["late"], -row["on_time"], row.get("name") or row.get("title") or ""),
        )

    return {
        "on_time": on_time,
        "late": late,
        "with_deadline": on_time + late,
        "students": _ranked(by_student),
        "tasks": _ranked(by_task),
    }


def get_timed_stats(db: DBSession, *, student_id: int | None = None) -> dict:
    """Контрольные на время: кто уложился в таймер, кто превысил, кто сдал
    после срока (владелец 03.09.2026: «будем отслеживать статистику, сколько
    детей превысили время… пометить красненьким»; сводка — 30.09.2026).

    Правила не свои: превышение — `task_blocks.timed_overrun`, опоздание —
    `task_blocks.completed_after_deadline`, те же функции рисуют отметки у
    ученика и на экране проверки, и цифры здесь с ними не разъедутся.

    `student_id` — один ученик (карточка): в `students` тогда каждая его
    контрольная, а не только с превышением или опозданием — уложился в
    таймер тоже ответ.
    """
    from app.models.task_block import BLOCK_TIMED, TaskBlock
    from app.services.task_blocks import (
        completed_after_deadline, get_submit_deadlines, get_task_submit_deadlines,
        timed_overrun,
    )

    now = datetime.now(timezone.utc)
    rows = (
        db.query(TaskBlockState, TaskBlock, TrackerTask, User.name, User.tariff)
        .join(TaskBlock, TaskBlock.id == TaskBlockState.block_id)
        .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
        .join(User, User.id == TaskBlockState.user_id)
        .filter(
            TaskBlock.block_type == BLOCK_TIMED,
            TaskBlockState.user_id.in_(_scope_ids(db, student_id)),
            or_(TaskBlockState.started_at.isnot(None), TaskBlockState.completed_at.isnot(None)),
            TrackerTask.deleted_at.is_(None),
        )
        .all()
    )
    block_deadlines = get_submit_deadlines(db, list({r[1].id for r in rows}))
    task_deadlines = get_task_submit_deadlines(db, list({r[2].id for r in rows}))

    by_block: dict[int, dict] = {}
    students: list[dict] = []
    for state, block, task, name, tariff in rows:
        title = f"{task.title} — {block.title}" if block.title else task.title
        item = by_block.setdefault(block.id, {
            "title": title, "limit": block.time_limit_minutes,
            "started": 0, "submitted": 0, "in_time": 0, "overrun": 0,
            "late": 0, "running_over": 0,
        })
        started = _utc(state.started_at)
        finished = _utc(state.completed_at)
        if started is not None:
            item["started"] += 1
        overrun = timed_overrun(block, state)
        late = completed_after_deadline(
            block, task, state, user_tariff=tariff,
            block_overrides=block_deadlines.get(block.id),
            task_overrides=task_deadlines.get(task.id),
        )
        if finished is not None:
            item["submitted"] += 1
            item["overrun" if overrun else "in_time"] += 1
            item["late"] += 1 if late else 0
        elif (
            started is not None and block.time_limit_minutes
            and (now - started).total_seconds() > block.time_limit_minutes * 60
        ):
            # Начал, время вышло, а работы нет — ещё рисует или бросил.
            item["running_over"] += 1
        if overrun or late or student_id is not None:
            students.append({
                "name": name, "tariff": tariff, "title": title,
                "minutes": (
                    int((finished - started).total_seconds() // 60)
                    if started is not None and finished is not None else None
                ),
                "limit": block.time_limit_minutes,
                "overrun": overrun, "late": late,
            })

    students.sort(key=lambda row: (row["title"], row["name"] or ""))
    blocks = sorted(by_block.values(), key=lambda row: row["title"])
    return {
        "blocks": blocks,
        "students": students,
        "submitted": sum(b["submitted"] for b in blocks),
        "overrun": sum(b["overrun"] for b in blocks),
        "late": sum(b["late"] for b in blocks),
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
    # «Дал ОС» (О21, вместо «оценено» у кураторов): в скольких диалогах
    # сотрудник написал хоть одно сообщение — пробник, старая домашка, сдача.
    feedback_given: dict[int, int] = defaultdict(int)
    for model in (FeedbackMessage, HomeworkFeedbackMessage, TaskBlockFeedbackMessage):
        for uid, n in (
            db.query(model.sender_id, func.count(func.distinct(model.feedback_id)))
            .filter(model.sender_id.in_(ids), model.sender_role != ROLE_STUDENT)
            .group_by(model.sender_id)
            .all()
        ):
            feedback_given[uid] += n
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
            "feedback_given": feedback_given.get(s.id, 0),
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


# ── Активность одного ученика (вкладка «Активность» в карточке «Учеников») ──
#
# Владелец 05.10.2026: всё про ученика — в его карточке, включая активность.
# Те же журналы, что у сводных карточек выше (`StudentActivityEvent`,
# `VideoProgress`, `VideoViewLog`, `AuditLog`), но с фильтром по одному
# человеку. Число запросов не зависит от объёма его истории: лента берёт
# по `FEED_LIMIT` строк из каждого журнала, остальное — агрегаты.

FEED_LIMIT = 20

_STUDENT_EVENT_FEED_LABELS = {
    "login": "Вошёл в кабинет",
    "portfolio_upload": "Загрузил работы в портфолио",
    "work_upload": "Загрузил работы",
}

# Действия сотрудников над учеником, которые попадают в его ленту. Остальные
# записи аудита (дайджест, программа) к одному ученику не относятся.
_STUDENT_AUDIT_ACTIONS = (
    "curator_assign", "tariff_change", "access_until_change", "payment_settings_change",
    "user_block", "user_unblock", "user_archive", "user_unarchive",
    "impersonate_start", "impersonate_stop",
)


def _curator_change_text(details: str | None, names: dict[int, str]) -> str | None:
    """«curator: 12 → 15» из журнала — в имена кураторов."""
    if not details or not details.startswith("curator:"):
        return details

    def _name(raw: str) -> str:
        raw = raw.strip()
        if not raw.isdigit():
            return "без куратора"
        return names.get(int(raw), f"id={raw}")

    old, _, new = details.removeprefix("curator:").partition("→")
    return f"{_name(old)} → {_name(new)}"


def student_activity(
    db: DBSession,
    student: User,
    *,
    today,
    days: int = RECENT_DAYS,
    with_staff_actions: bool = False,
) -> dict:
    """Входы и загрузки, видео, задания со сроками и лента событий одного
    ученика.

    `with_staff_actions` — показывать ли в ленте действия сотрудников (смена
    тарифа и куратора, блок, архив, вход «глазами») с их именами. Только для
    ранга 4 и выше: куратор видит, что делал ученик, а не кто из сотрудников
    его правил.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Входы и загрузки за `days` дней.
    counts = dict(
        db.query(StudentActivityEvent.event_type, func.count(StudentActivityEvent.id))
        .filter(
            StudentActivityEvent.user_id == student.id,
            StudentActivityEvent.created_at >= since,
        )
        .group_by(StudentActivityEvent.event_type)
        .all()
    )

    # Видео: строка `VideoProgress` на каждый начатый ролик, открытия плеера —
    # в `VideoViewLog`.
    started, completed = (
        db.query(
            func.count(VideoProgress.video_id),
            func.count(VideoProgress.completed_at),
        )
        .filter(VideoProgress.user_id == student.id)
        .one()
    )
    opens, last_opened = (
        db.query(func.count(VideoViewLog.id), func.max(VideoViewLog.opened_at))
        .filter(VideoViewLog.user_id == student.id, VideoViewLog.opened_at >= since)
        .one()
    )
    if last_opened is None:
        last_opened = (
            db.query(func.max(VideoViewLog.opened_at))
            .filter(VideoViewLog.user_id == student.id)
            .scalar()
        )

    # Задания — тот же расчёт, что «Личный трекер» ученика (`cabinet_tracker.py`):
    # долг копится с начала, впереди — до конца этой недели.
    monday = week_start(today)
    _, week_end = day_bounds(monday + timedelta(days=6))
    entries = accessible_task_entries(
        db, student.id, start=None, end=week_end, include_undated=True,
    )
    by_status: dict[str, int] = defaultdict(int)
    for entry in entries:
        by_status[entry["status"]] += 1

    # Лента: последние события ученика и, для старших, действия над ним.
    feed: list[dict] = [
        {
            "at": _utc(e.created_at),
            "kind": "student",
            "label": _STUDENT_EVENT_FEED_LABELS.get(e.event_type, e.event_type),
            "details": e.details if e.event_type != "login" else None,
        }
        for e in (
            db.query(StudentActivityEvent)
            .filter(StudentActivityEvent.user_id == student.id)
            .order_by(StudentActivityEvent.created_at.desc())
            .limit(FEED_LIMIT)
            .all()
        )
    ]
    if with_staff_actions:
        audit_rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.target_user_id == student.id,
                AuditLog.action.in_(_STUDENT_AUDIT_ACTIONS),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(FEED_LIMIT)
            .all()
        )
        curator_ids = set()
        for r in audit_rows:
            if r.action == "curator_assign" and r.details:
                curator_ids.update(
                    int(x) for x in r.details.removeprefix("curator:").replace("→", " ").split()
                    if x.isdigit()
                )
        names = _names_by_id(db, {r.performed_by_id for r in audit_rows} | curator_ids)
        for r in audit_rows:
            details = r.details
            if r.action == "curator_assign":
                details = _curator_change_text(details, names)
            elif r.action.startswith("impersonate_"):
                details = None  # там номер служебной сессии
            feed.append({
                "at": _utc(r.created_at),
                "kind": "staff",
                "label": _AUDIT_LABELS.get(r.action, r.action),
                "details": details,
                "by": names.get(r.performed_by_id, f"id={r.performed_by_id}"),
            })
    feed.sort(key=lambda item: item["at"] or ACTIVITY_STATS_START, reverse=True)
    feed = feed[:FEED_LIMIT]
    for item in feed:
        item["at"] = _msk(item["at"]).strftime("%d.%m.%Y %H:%M") if item["at"] else ""

    def _when(value) -> str:
        value = _msk(_utc(value))
        return value.strftime("%d.%m.%Y %H:%M") if value else ""

    return {
        "days": days,
        "last_login": _when(student.last_login_at),
        "logins": counts.get("login", 0),
        "uploads": counts.get("portfolio_upload", 0) + counts.get("work_upload", 0),
        "video": {
            "started": started or 0,
            "completed": completed or 0,
            "opens": opens or 0,
            "last_opened": _when(last_opened),
        },
        "tasks": {
            "done": by_status.get("done", 0),
            "overdue": by_status.get("overdue", 0),
            "upcoming": by_status.get("upcoming", 0),
            "total": len(entries),
            # Красное «отстаёт» в трекере ученика (решение владельца 23.08):
            # первая незакрытая неделя раньше текущей.
            "behind": effective_week_start(db, student.id, today) < monday,
        },
        "feed": feed,
    }


def student_statistics(
    db: DBSession,
    student: User,
    *,
    today,
    with_staff_actions: bool = False,
    with_school_stats: bool = False,
) -> dict:
    """Вкладка «Статистика» карточки ученика: всё про ребёнка (владелец
    06.10.2026: «собрать туда всё, что касается ребёнка из вкладки статистика,
    которая у нас есть на главном дашборде»). «Активность» слита сюда же.

    `activity` — то, что видит каждый, кто открыл карточку, включая куратора
    (владелец: «оставить показ данных, как сейчас у куратора»).
    `school` — разделы «Статистики активности» для одного ученика, только рангу
    ≥ 4, как и сама та страница. Своих расчётов здесь нет: те же функции
    с `student_id`, что кормят дашборд, иначе цифры карточки и дашборда
    разъехались бы.
    """
    from app.services.staff_dashboard import student_assignments

    result = {
        "activity": student_activity(
            db, student, today=today, with_staff_actions=with_staff_actions,
        ),
        "school": None,
    }
    if not with_school_stats:
        return result

    deadlines = get_deadline_stats(db, student_id=student.id)
    timed = get_timed_stats(db, student_id=student.id)
    video = get_video_watch_stats(db, student_id=student.id)
    result["school"] = {
        "assignments": student_assignments(db, student),
        "deadlines": {
            "on_time": deadlines["on_time"],
            "late": deadlines["late"],
            "late_tasks": [t for t in deadlines["tasks"] if t["late"]],
        },
        "timed": timed["students"],
        "diagnostics": [
            {
                "title": item["task"].title,
                "status": row["status_label"],
                "started": row["started_at_text"],
                "finished": row["finished_at_text"],
                "duration": row["duration_text"],
                "profile": row["profile"]["title"] if row["profile"] else None,
            }
            for item in get_diagnostic_stats(db, student_id=student.id)
            for row in item["rows"]
        ],
        "videos": video["videos"],
        "video_days": video["days"],
    }
    return result
