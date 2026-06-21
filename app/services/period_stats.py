"""
Агрегированная статистика по периодам сдачи работ.
Используется в кабинете суперадмина.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS
from app.models.exam_assignment import ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.feature_period import FeaturePeriod
from app.models.feedback import Feedback, FeedbackMessage
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.role import Role
from app.models.user import User
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.feedback import ROLE_STUDENT, role_label_ru
from app.services.tz import MSK_TZ, msk_midnight


# Статистика пробников ведётся с этой даты: раньше не было обратной связи по
# пробникам, и более ранние сдачи/выдачи билетов не учитываем во всех секциях.
MOCK_STATS_START = date(2026, 6, 13)

# Диапазоны баллов для разреза «Статистика по баллам» — конкретные интервалы,
# заданные владельцем; промежутки между диапазонами не отображаются.
MOCK_SCORE_RANGES = [(0, 50), (55, 65), (70, 75), (80, 85)]


def _to_msk(dt):
    """UTC/naive datetime → MSK. func.max() в SQLite может вернуть naive — тогда
    считаем UTC (как student_score_curve)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK_TZ)


def _mock_window(db: DBSession, feature: str | None, period_id: int | None):
    """Гейт «раздел про пробники применим?» + окно дат (MSK) с floor 13.06.2026.

    Возвращает (applicable, start_dt, end_dt). Раздел применим к виду «Все работы»,
    периоду/фиче mock_exam; для portfolio_upload / retake — applicable=False.
    start_dt всегда задан (не раньше MOCK_STATS_START); end_dt задан только для
    выбранного периода.
    """
    floor = msk_midnight(MOCK_STATS_START)
    if period_id:
        period = db.query(FeaturePeriod).filter(FeaturePeriod.id == period_id).first()
        if period:
            if period.feature != "mock_exam":
                return False, None, None
            start = max(msk_midnight(period.start_date), floor)
            return True, start, msk_midnight(period.end_date + timedelta(days=1))
    elif feature and feature != "mock_exam":
        return False, None, None
    return True, floor, None


def get_ticket_receipt_stats(
    db: DBSession,
    feature: str | None = None,
    period_id: int | None = None,
) -> dict:
    """
    Кто из учеников какой билет получил на пробнике.

    «Получил билет» = момент клика «Начать пробник» → создаётся MockExamAttempt
    со снимком билета (ticket_title) и временем (started_at). Это и есть
    «сколько учеников какой билет получили, дата и время, с названием билета».

    Данные относятся только к пробникам, поэтому раздел применим лишь к виду
    «Все работы» или к периоду/фиче mock_exam. Для portfolio_upload / retake
    возвращаем applicable=False — раздел в шаблоне скрывается.

    Окно: started_at >= 13.06.2026 (MOCK_STATS_START), а при выбранном периоде —
    в его границах (MSK).

    Возвращает:
    {
      "applicable": bool,
      "total_students": int,                  # уникальных учеников, получивших билет
      "total_receipts": int,                  # всего выдач билетов (попыток)
      "by_ticket": [                          # агрегат по билету, по убыванию учеников
          {"ticket_title", "subject", "student_count", "attempt_count", "last_at", "deleted"}
      ],
      "receipts": [                           # детально, свежие сверху (до 500)
          {"student_name", "student_id", "ticket_title", "subject", "started_at", "completed"}
      ],
    }
    """
    applicable, start_dt, end_dt = _mock_window(db, feature, period_id)
    if not applicable:
        return {
            "applicable": False,
            "total_students": 0,
            "total_receipts": 0,
            "by_ticket": [],
            "receipts": [],
        }

    M = MockExamAttempt

    def _window(q):
        if start_dt is not None:
            q = q.filter(M.started_at >= start_dt)
        if end_dt is not None:
            q = q.filter(M.started_at < end_dt)
        return q

    # ── Точные итоги и агрегат по билету — отдельными запросами без лимита ─────
    # (детальный список ниже капается 500-ю, итоги — нет, иначе при >500 выдач
    #  «сколько учеников получили» молча занижается; ср. mock_q в submission stats)
    total_students = _window(db.query(func.count(func.distinct(M.user_id)))).scalar() or 0
    total_receipts = _window(db.query(func.count(M.id))).scalar() or 0

    # Живые билеты — группировка по ticket_id (стабилен, в отличие от снимка title).
    live_rows = (
        _window(db.query(
            func.max(M.ticket_title).label("title"),
            func.max(M.subject).label("subject"),
            func.count(func.distinct(M.user_id)).label("students"),
            func.count(M.id).label("attempts"),
            func.max(M.started_at).label("last_at"),
        ).filter(M.ticket_id.isnot(None)))
        .group_by(M.ticket_id)
        .all()
    )
    # Удалённые билеты (ticket_id IS NULL) — группировка по названию снимка,
    # чтобы разные удалённые билеты не схлопнулись в одну строку.
    deleted_rows = (
        _window(db.query(
            M.ticket_title.label("title"),
            func.max(M.subject).label("subject"),
            func.count(func.distinct(M.user_id)).label("students"),
            func.count(M.id).label("attempts"),
            func.max(M.started_at).label("last_at"),
        ).filter(M.ticket_id.is_(None)))
        .group_by(M.ticket_title)
        .all()
    )

    by_ticket = [
        {
            "ticket_title": r.title,
            "subject": r.subject or "",
            "student_count": int(r.students),
            "attempt_count": int(r.attempts),
            "last_at": _to_msk(r.last_at),
            "deleted": deleted,
        }
        for deleted, group in ((False, live_rows), (True, deleted_rows))
        for r in group
    ]
    by_ticket.sort(key=lambda t: (t["student_count"], t["attempt_count"]), reverse=True)

    # ── Детальный список: свежие сверху, до 500 ───────────────────────────────
    detail_rows = (
        _window(db.query(M, User).join(User, M.user_id == User.id))
        .order_by(M.started_at.desc())
        .limit(500)
        .all()
    )
    receipts = [
        {
            "student_name": f"{u.last_name or ''} {u.first_name or u.name}".strip(),
            "student_id": u.id,
            "ticket_title": attempt.ticket_title,
            "subject": attempt.subject or "",
            "started_at": _to_msk(attempt.started_at),
            "completed": attempt.completed_at is not None,
        }
        for attempt, u in detail_rows
    ]

    return {
        "applicable": True,
        "total_students": total_students,
        "total_receipts": total_receipts,
        "by_ticket": by_ticket,
        "receipts": receipts,
    }


def get_mock_feedback_rows(
    db: DBSession,
    feature: str | None = None,
    period_id: int | None = None,
    limit: int | None = 500,
) -> dict:
    """
    Сводная таблица по пробникам и обратной связи: одна строка на финальную
    сдачу пробника (Work: mock_exam, is_final, success) в окне периода.

    Колонки: ученик, предмет, билет, балл, куратор, время сдачи, время ОС и весь
    текст обратной связи (диалог). Это «внести в таблицу всю обратную связь, баллы,
    учеников и кураторов, название билета и время сдачи, время обратной связи».

    Применимо только к пробникам (см. _mock_window) — иначе applicable=False.

    Связи:
      • билет   — Work.cycle_id → ExamCycle.ticket_id → ExamTicket.title.
                  Best-effort: при редактировании задания ticket_id у цикла
                  обнуляется (cabinet_superadmin), поэтому для части истории
                  название билета будет «—».
      • куратор — Feedback.curator_id (автор ОС), иначе Work.scored_by_id (кто
                  выставил балл).
      • время ОС— время первого сообщения staff, иначе Feedback.created_at.

    Итоги (total / with_feedback / avg_score) считаются отдельными запросами без
    лимита; детальный список — limit строк (свежие сверху). limit=None → без
    лимита (для Excel-экспорта, где нужна «вся» таблица).
    """
    applicable, start_dt, end_dt = _mock_window(db, feature, period_id)
    if not applicable:
        return {
            "applicable": False,
            "total": 0,
            "with_feedback": 0,
            "avg_score": None,
            "rows": [],
        }

    conds = [
        Work.work_type == WORK_TYPE_MOCK_EXAM,
        Work.is_final == True,  # noqa: E712
        Work.status == "success",
    ]
    if start_dt is not None:
        conds.append(Work.created_at >= start_dt)
    if end_dt is not None:
        conds.append(Work.created_at < end_dt)

    # ── Точные итоги (без лимита) ─────────────────────────────────────────────
    total = db.query(func.count(Work.id)).filter(*conds).scalar() or 0
    avg_raw = (
        db.query(func.avg(Work.score))
        .filter(*conds, Work.score.isnot(None))
        .scalar()
    )
    avg_score = round(float(avg_raw), 1) if avg_raw is not None else None
    with_feedback = (
        db.query(func.count(func.distinct(Feedback.work_id)))
        .join(Work, Feedback.work_id == Work.id)
        .filter(*conds)
        .scalar() or 0
    )

    # ── Детальный список (до 500, свежие сверху) ──────────────────────────────
    work_user_q = (
        db.query(Work, User)
        .join(User, Work.user_id == User.id)
        .filter(*conds)
        .order_by(Work.created_at.desc())
    )
    if limit is not None:
        work_user_q = work_user_q.limit(limit)
    work_user = work_user_q.all()
    works = [w for w, _ in work_user]
    work_ids = [w.id for w in works]
    cycle_ids = [w.cycle_id for w in works if w.cycle_id]

    # Названия билетов через цикл (outer join — ticket_id может быть NULL/удалён)
    ticket_by_cycle: dict[int, str | None] = {}
    if cycle_ids:
        for cid, title in (
            db.query(ExamCycle.id, ExamTicket.title)
            .outerjoin(ExamTicket, ExamCycle.ticket_id == ExamTicket.id)
            .filter(ExamCycle.id.in_(cycle_ids))
            .all()
        ):
            ticket_by_cycle[cid] = title

    # Контейнеры ОС + сообщения диалога
    fb_by_work: dict[int, Feedback] = {}
    if work_ids:
        for fb in db.query(Feedback).filter(Feedback.work_id.in_(work_ids)).all():
            fb_by_work[fb.work_id] = fb
    fb_ids = [fb.id for fb in fb_by_work.values()]
    msgs_by_fb: dict[int, list[FeedbackMessage]] = defaultdict(list)
    if fb_ids:
        for m in (
            db.query(FeedbackMessage)
            .filter(FeedbackMessage.feedback_id.in_(fb_ids))
            .order_by(FeedbackMessage.created_at, FeedbackMessage.id)
            .all()
        ):
            msgs_by_fb[m.feedback_id].append(m)

    # Имена: кураторы, кто выставил балл, авторы сообщений
    need_ids: set[int] = set()
    for w in works:
        if w.scored_by_id:
            need_ids.add(w.scored_by_id)
    for fb in fb_by_work.values():
        need_ids.add(fb.curator_id)
    for msgs in msgs_by_fb.values():
        for m in msgs:
            need_ids.add(m.sender_id)
    name_by_id: dict[int, str] = {}
    if need_ids:
        for uid, fn, ln, nm in (
            db.query(User.id, User.first_name, User.last_name, User.name)
            .filter(User.id.in_(need_ids))
            .all()
        ):
            name_by_id[uid] = f"{ln or ''} {fn or nm}".strip()

    def _compose_feedback(msgs: list[FeedbackMessage]) -> str:
        parts: list[str] = []
        for m in msgs:
            label = role_label_ru(m.sender_role)
            who = name_by_id.get(m.sender_id) or label
            if m.text:
                body = m.text
            elif m.photo_s3_url:
                body = "[фото]"
            else:
                continue
            ts = _to_msk(m.created_at)
            stamp = f", {ts.strftime('%d.%m.%Y %H:%M')}" if ts else ""
            parts.append(f"{who} ({label}{stamp}): {body}")
        return "\n\n".join(parts)

    rows: list[dict] = []
    for w, u in work_user:
        fb = fb_by_work.get(w.id)
        msgs = msgs_by_fb.get(fb.id, []) if fb else []
        staff_msgs = [m for m in msgs if m.sender_role != ROLE_STUDENT]
        feedback_at = staff_msgs[0].created_at if staff_msgs else (fb.created_at if fb else None)
        curator_id = fb.curator_id if fb else w.scored_by_id
        rows.append({
            "work_id": w.id,
            "student_name": f"{u.last_name or ''} {u.first_name or u.name}".strip(),
            "student_id": u.id,
            "subject": w.subject or "",
            "ticket_title": ticket_by_cycle.get(w.cycle_id),
            "score": float(w.score) if w.score is not None else None,
            "curator_name": name_by_id.get(curator_id) if curator_id else None,
            "submitted_at": _to_msk(w.created_at),
            "feedback_at": _to_msk(feedback_at),
            "feedback_text": _compose_feedback(msgs),
            "has_feedback": fb is not None,
        })

    return {
        "applicable": True,
        "total": total,
        "with_feedback": with_feedback,
        "avg_score": avg_score,
        "rows": rows,
    }


def get_mock_subject_status(
    db: DBSession,
    feature: str | None = None,
    period_id: int | None = None,
) -> dict:
    """
    Статус сдачи пробников по каждому ученику, разрез по тарифу.

    Для каждого активного ученика (rank=1): сдал ли он пробник по «Рисунку» и
    «Композиции» (Work: mock_exam, success) в окне от 13.06.2026 (см. _mock_window),
    при выбранном периоде — в его границах.

    Лёгкая функция для страницы статистики пробников: считает только два разреза
    (статус по предметам + кто не сдал), без by_type/by_tariff/submissions.

    Возвращает:
    {
      "applicable": bool,
      "by_tariff_mock_status": {tariff: [{student_id, student_name, vk_id, tg_username, risunok, kompoziciya}]},
      "not_submitted_by_tariff": {tariff: [...]},   # не сдал хотя бы один предмет
    }
    """
    applicable, start_dt, end_dt = _mock_window(db, feature, period_id)
    if not applicable:
        return {
            "applicable": False,
            "by_tariff_mock_status": {},
            "not_submitted_by_tariff": {},
        }

    mock_q = db.query(Work.user_id, Work.subject).filter(
        Work.status == "success",
        Work.work_type == WORK_TYPE_MOCK_EXAM,
        Work.subject.isnot(None),
    )
    if start_dt is not None:
        mock_q = mock_q.filter(Work.created_at >= start_dt)
    if end_dt is not None:
        mock_q = mock_q.filter(Work.created_at < end_dt)
    mock_submitted: set[tuple] = {(uid, subj) for uid, subj in mock_q.all()}

    all_students = (
        db.query(User.id, User.first_name, User.last_name, User.name,
                 User.vk_id, User.tg_username, User.tariff)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == 1, User.is_active == True, User.deleted_at.is_(None))  # noqa: E712
        .order_by(User.last_name, User.first_name)
        .all()
    )

    by_tariff_mock_status: dict[str, list] = defaultdict(list)
    not_submitted_by_tariff: dict[str, list] = defaultdict(list)
    for r in all_students:
        tar = r.tariff or "—"
        has_risunok = (r.id, "Рисунок") in mock_submitted
        has_kompoziciya = (r.id, "Композиция") in mock_submitted
        entry = {
            "student_id": r.id,
            "student_name": f"{r.last_name or ''} {r.first_name or r.name}".strip(),
            "vk_id": r.vk_id,
            "tg_username": r.tg_username or "",
            "risunok": has_risunok,
            "kompoziciya": has_kompoziciya,
        }
        by_tariff_mock_status[tar].append(entry)
        if not has_risunok or not has_kompoziciya:
            not_submitted_by_tariff[tar].append(entry)

    return {
        "applicable": True,
        "by_tariff_mock_status": dict(by_tariff_mock_status),
        "not_submitted_by_tariff": dict(not_submitted_by_tariff),
    }


def get_all_periods(db: DBSession) -> list[FeaturePeriod]:
    """Все периоды, отсортированные по убыванию даты начала."""
    return (
        db.query(FeaturePeriod)
        .order_by(FeaturePeriod.start_date.desc())
        .limit(100)
        .all()
    )


def get_mock_score_stats(
    db: DBSession,
    feature: str | None = None,
    period_id: int | None = None,
) -> dict:
    """
    Статистика по баллам финальных сдач пробников, разрез по предмету.

    Для каждого предмета (Рисунок, Композиция) считаем уникальных учеников
    (Work: mock_exam, is_final, success) в окне периода:
      • total  — сколько учеников сдали предмет;
      • ranges — сколько из них попало в каждый диапазон MOCK_SCORE_RANGES (включительно
        с обеих сторон). Если у ученика несколько финальных сдач с баллами в разных
        диапазонах, он будет учтён в каждом из них — поэтому сумма ranges может
        превышать total.

    Окно и применимость — как в остальных секциях статистики пробников (см. _mock_window).
    """
    applicable, start_dt, end_dt = _mock_window(db, feature, period_id)
    if not applicable:
        return {"applicable": False, "by_subject": {}}

    conds = [
        Work.work_type == WORK_TYPE_MOCK_EXAM,
        Work.is_final == True,  # noqa: E712
        Work.status == "success",
    ]
    if start_dt is not None:
        conds.append(Work.created_at >= start_dt)
    if end_dt is not None:
        conds.append(Work.created_at < end_dt)

    by_subject: dict[str, dict] = {}
    for subject in MOCK_SUBJECTS:
        subj_conds = conds + [Work.subject == subject]
        total = db.query(func.count(func.distinct(Work.user_id))).filter(*subj_conds).scalar() or 0
        ranges = []
        for lo, hi in MOCK_SCORE_RANGES:
            count = (
                db.query(func.count(func.distinct(Work.user_id)))
                .filter(*subj_conds, Work.score >= lo, Work.score <= hi)
                .scalar() or 0
            )
            ranges.append({"label": f"{lo}–{hi}", "min": lo, "max": hi, "count": count})
        by_subject[subject] = {"total": total, "ranges": ranges}

    return {"applicable": True, "by_subject": by_subject}
