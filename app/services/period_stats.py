"""
Агрегированная статистика по периодам сдачи работ.
Используется в кабинете суперадмина.
"""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from app.models.feature_period import FeaturePeriod
from app.models.role import Role
from app.models.user import User
from app.models.work import Work


def get_submission_stats(
    db: DBSession,
    feature: str | None = None,
    period_id: int | None = None,
) -> dict:
    """
    Статистика сдач за конкретный период FeaturePeriod.

    Возвращает:
    {
      "period": FeaturePeriod | None,
      "total": int,
      "by_type": {work_type: count},
      "by_tariff": {tariff: count},
      "by_mock_subject": {"Рисунок": count, "Композиция": count},
      "by_tariff_mock_status": {tariff: [{student_id, student_name, vk_id, tg_username, risunok, kompoziciya}]},
      "not_submitted_by_tariff": {tariff: [...]},   # не сдал хотя бы один предмет пробника
      "timeline": [{"date": "YYYY-MM-DD", "count": int}],
      "submissions": [...],
    }
    """
    period: FeaturePeriod | None = None
    start_dt: datetime | None = None
    end_dt: datetime | None = None

    q = db.query(Work, User).join(User, Work.user_id == User.id).filter(Work.status == "success")

    if period_id:
        period = db.query(FeaturePeriod).filter(FeaturePeriod.id == period_id).first()
        if period:
            start_dt = datetime(period.start_date.year, period.start_date.month, period.start_date.day, tzinfo=timezone.utc)
            end_dt = datetime(period.end_date.year, period.end_date.month, period.end_date.day, 23, 59, 59, tzinfo=timezone.utc)
            q = q.filter(Work.created_at >= start_dt, Work.created_at <= end_dt)
            if period.feature in ("portfolio_upload", "retake"):
                q = q.filter(Work.work_type.in_(["before", "after", "retake"]))
            elif period.feature == "mock_exam":
                q = q.filter(Work.work_type == "mock_exam")
    elif feature:
        if feature in ("portfolio_upload", "retake"):
            q = q.filter(Work.work_type.in_(["before", "after", "retake"]))
        elif feature == "mock_exam":
            q = q.filter(Work.work_type == "mock_exam")

    rows = q.order_by(Work.created_at.desc()).limit(500).all()

    by_type: dict[str, int] = {}
    by_tariff: dict[str, int] = {}
    timeline_map: dict[str, int] = {}
    submissions = []

    for w, u in rows:
        by_type[w.work_type] = by_type.get(w.work_type, 0) + 1
        t = w.tariff or u.tariff or "—"
        by_tariff[t] = by_tariff.get(t, 0) + 1

        day = w.created_at.strftime("%Y-%m-%d") if w.created_at else "—"
        timeline_map[day] = timeline_map.get(day, 0) + 1

        submissions.append({
            "student_name": f"{u.last_name or ''} {u.first_name or u.name}".strip(),
            "student_id": u.id,
            "vk_id": u.vk_id,
            "tg_username": u.tg_username,
            "work_type": w.work_type,
            "subject": w.subject or "",
            "tariff": t,
            "created_at": w.created_at,
            "score": float(w.score) if w.score is not None else None,
        })

    timeline = sorted(
        [{"date": d, "count": c} for d, c in timeline_map.items()],
        key=lambda x: x["date"],
    )

    # Пробники по предмету (из 500 записей submissions)
    by_mock_subject: dict[str, int] = {}
    for s in submissions:
        if s["work_type"] == "mock_exam" and s["subject"]:
            by_mock_subject[s["subject"]] = by_mock_subject.get(s["subject"], 0) + 1

    # ── Статус пробников по каждому ученику ───────────────────────────────────
    # Запрос без лимита 500 — нужны ВСЕ сданные пробники (по предмету)
    mock_q = db.query(Work.user_id, Work.subject).filter(
        Work.status == "success",
        Work.work_type == "mock_exam",
        Work.subject.isnot(None),
    )
    if start_dt and end_dt:
        mock_q = mock_q.filter(Work.created_at >= start_dt, Work.created_at <= end_dt)

    mock_submitted: set[tuple] = {(uid, subj) for uid, subj in mock_q.all()}

    # Все активные ученики (rank=1)
    all_students = (
        db.query(User.id, User.first_name, User.last_name, User.name,
                 User.vk_id, User.tg_username, User.tariff)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == 1, User.is_active == True, User.deleted_at.is_(None))
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
        # Не сдал хотя бы один предмет пробника
        if not has_risunok or not has_kompoziciya:
            not_submitted_by_tariff[tar].append(entry)

    return {
        "period": period,
        "total": len(rows),
        "by_type": by_type,
        "by_tariff": by_tariff,
        "by_mock_subject": by_mock_subject,
        "by_tariff_mock_status": dict(by_tariff_mock_status),
        "not_submitted_by_tariff": dict(not_submitted_by_tariff),
        "timeline": timeline,
        "submissions": submissions,
    }


def get_all_periods(db: DBSession) -> list[FeaturePeriod]:
    """Все периоды, отсортированные по убыванию даты начала."""
    return (
        db.query(FeaturePeriod)
        .order_by(FeaturePeriod.start_date.desc())
        .limit(100)
        .all()
    )
