"""Tests for app/services/stats.py — dashboard aggregations."""
from datetime import datetime, timedelta, timezone, date

import pytest

from app.constants import FEATURE_MOCK_EXAM
from app.models.feature_period import FeaturePeriod
from app.models.user import User
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.stats import (
    mock_period_subject_stats,
    score_curve_12m,
    curator_avg_scores,
)


def _make_work(
    db,
    *,
    user_id: int,
    subject: str,
    score: float | None,
    created_at: datetime,
    work_type: str = WORK_TYPE_MOCK_EXAM,
):
    w = Work(
        user_id=user_id,
        work_type=work_type,
        month="март",
        year=created_at.year,
        filename=f"w-{user_id}-{subject}-{created_at.isoformat()}.jpg",
        subject=subject,
        score=score,
        status="success",
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    # explicit override т.к. created_at имеет default=now
    w.created_at = created_at
    db.commit()
    return w


def _make_active_period(db, admin_id: int, days_back: int = 7, days_forward: int = 7):
    today = date.today()
    p = FeaturePeriod(
        feature=FEATURE_MOCK_EXAM,
        start_date=today - timedelta(days=days_back),
        end_date=today + timedelta(days=days_forward),
        is_active=True,
        created_by_id=admin_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# ─── mock_period_subject_stats ───────────────────────────────────────────────

def test_mock_period_stats_no_active_period(db, user_factory):
    """Без активного периода: period=None, scored=0, total=число учеников."""
    user_factory(vk_id=1, role_name="ученик")
    user_factory(vk_id=2, role_name="ученик")
    period, stats = mock_period_subject_stats(db)
    assert period is None
    assert {s["subject"] for s in stats} == {"Рисунок", "Композиция"}
    assert all(s["scored"] == 0 for s in stats)
    assert all(s["total"] == 2 for s in stats)


def test_mock_period_stats_counts_scored_works(db, user_factory):
    admin = user_factory(vk_id=999, role_name="суперадмин")
    s1 = user_factory(vk_id=1, role_name="ученик")
    s2 = user_factory(vk_id=2, role_name="ученик")
    s3 = user_factory(vk_id=3, role_name="ученик")
    _make_active_period(db, admin.id)

    now = datetime.now(timezone.utc)
    # s1 — Рисунок оценён, Композиция не оценена (None)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=70, created_at=now)
    _make_work(db, user_id=s1.id, subject="Композиция", score=None, created_at=now)
    # s2 — обе оценены
    _make_work(db, user_id=s2.id, subject="Рисунок", score=80, created_at=now)
    _make_work(db, user_id=s2.id, subject="Композиция", score=85, created_at=now)
    # s3 — ничего

    period, stats = mock_period_subject_stats(db)
    by_subj = {s["subject"]: s for s in stats}
    assert by_subj["Рисунок"]["scored"] == 2
    assert by_subj["Композиция"]["scored"] == 1
    assert by_subj["Рисунок"]["total"] == 3
    assert by_subj["Композиция"]["total"] == 3
    assert period is not None


def test_mock_period_stats_filters_by_period_dates(db, user_factory):
    admin = user_factory(vk_id=999, role_name="суперадмин")
    s1 = user_factory(vk_id=1, role_name="ученик")
    _make_active_period(db, admin.id)

    # Работа ДО начала периода — не должна считаться
    long_ago = datetime.now(timezone.utc) - timedelta(days=90)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=70, created_at=long_ago)

    period, stats = mock_period_subject_stats(db)
    by_subj = {s["subject"]: s for s in stats}
    assert by_subj["Рисунок"]["scored"] == 0


def test_mock_period_stats_curator_filter(db, user_factory):
    """С curator_id — только ученики этого куратора."""
    admin = user_factory(vk_id=999, role_name="суперадмин")
    curator = user_factory(vk_id=500, role_name="куратор")
    other_curator = user_factory(vk_id=501, role_name="куратор")

    s1 = user_factory(vk_id=1, role_name="ученик")
    s1.curator_id = curator.id
    s2 = user_factory(vk_id=2, role_name="ученик")
    s2.curator_id = curator.id
    s3 = user_factory(vk_id=3, role_name="ученик")
    s3.curator_id = other_curator.id
    db.commit()

    _make_active_period(db, admin.id)
    now = datetime.now(timezone.utc)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=70, created_at=now)
    _make_work(db, user_id=s3.id, subject="Рисунок", score=70, created_at=now)

    _period, stats = mock_period_subject_stats(db, curator_id=curator.id)
    by_subj = {s["subject"]: s for s in stats}
    assert by_subj["Рисунок"]["scored"] == 1   # только s1
    assert by_subj["Рисунок"]["total"] == 2    # s1+s2


def test_mock_period_stats_distinct_users(db, user_factory):
    """Несколько оценённых работ одного ученика — считается один раз."""
    admin = user_factory(vk_id=999, role_name="суперадмин")
    s1 = user_factory(vk_id=1, role_name="ученик")
    _make_active_period(db, admin.id)
    now = datetime.now(timezone.utc)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=70, created_at=now)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=80, created_at=now - timedelta(days=1))

    _period, stats = mock_period_subject_stats(db)
    by_subj = {s["subject"]: s for s in stats}
    assert by_subj["Рисунок"]["scored"] == 1


# ─── score_curve_12m ─────────────────────────────────────────────────────────

def test_score_curve_returns_12_points(db, user_factory):
    points = score_curve_12m(db)
    assert len(points) == 12
    # все ключи присутствуют
    for p in points:
        assert {"label", "year", "month", "drawing", "composition"} <= set(p.keys())
        assert p["drawing"] is None
        assert p["composition"] is None


def test_score_curve_aggregates_by_month_and_subject(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    now = datetime.now(timezone.utc)
    # Текущий месяц — две работы по Рисунку: средняя должна быть 75
    _make_work(db, user_id=s1.id, subject="Рисунок", score=70, created_at=now)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=80, created_at=now)
    _make_work(db, user_id=s1.id, subject="Композиция", score=60, created_at=now)

    points = score_curve_12m(db)
    last = points[-1]
    assert last["year"] == now.year
    assert last["month"] == now.month
    assert last["drawing"] == 75.0
    assert last["composition"] == 60.0


def test_score_curve_excludes_unscored(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    now = datetime.now(timezone.utc)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=None, created_at=now)
    points = score_curve_12m(db)
    assert points[-1]["drawing"] is None


def test_score_curve_excludes_old_data(db, user_factory):
    """Работы старше 12 месяцев не должны попадать в выборку."""
    s1 = user_factory(vk_id=1, role_name="ученик")
    long_ago = datetime.now(timezone.utc) - timedelta(days=400)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=90, created_at=long_ago)

    points = score_curve_12m(db)
    # ни одна точка не должна содержать 90.0
    assert all(p["drawing"] != 90.0 for p in points)


# ─── curator_avg_scores ──────────────────────────────────────────────────────

def test_curator_avg_scores_empty(db, user_factory):
    curator = user_factory(vk_id=500, role_name="куратор")
    result = curator_avg_scores(db, curator_id=curator.id)
    assert result == {"Рисунок": None, "Композиция": None}


def test_curator_avg_scores_all_time(db, user_factory):
    curator = user_factory(vk_id=500, role_name="куратор")
    s1 = user_factory(vk_id=1, role_name="ученик")
    s1.curator_id = curator.id
    s2 = user_factory(vk_id=2, role_name="ученик")
    s2.curator_id = curator.id
    db.commit()

    long_ago = datetime.now(timezone.utc) - timedelta(days=200)
    now = datetime.now(timezone.utc)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=60, created_at=long_ago)
    _make_work(db, user_id=s2.id, subject="Рисунок", score=80, created_at=now)
    _make_work(db, user_id=s1.id, subject="Композиция", score=50, created_at=now)

    result = curator_avg_scores(db, curator_id=curator.id)
    assert result["Рисунок"] == 70.0   # (60+80)/2 — даже старые попадают (за всё время)
    assert result["Композиция"] == 50.0


def test_curator_avg_scores_excludes_other_curators(db, user_factory):
    curator = user_factory(vk_id=500, role_name="куратор")
    other = user_factory(vk_id=501, role_name="куратор")
    s1 = user_factory(vk_id=1, role_name="ученик")
    s1.curator_id = curator.id
    s2 = user_factory(vk_id=2, role_name="ученик")
    s2.curator_id = other.id
    db.commit()

    now = datetime.now(timezone.utc)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=60, created_at=now)
    _make_work(db, user_id=s2.id, subject="Рисунок", score=100, created_at=now)

    result = curator_avg_scores(db, curator_id=curator.id)
    assert result["Рисунок"] == 60.0


def test_curator_avg_scores_ignores_unscored(db, user_factory):
    curator = user_factory(vk_id=500, role_name="куратор")
    s1 = user_factory(vk_id=1, role_name="ученик")
    s1.curator_id = curator.id
    db.commit()

    now = datetime.now(timezone.utc)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=None, created_at=now)
    _make_work(db, user_id=s1.id, subject="Рисунок", score=80, created_at=now)

    result = curator_avg_scores(db, curator_id=curator.id)
    assert result["Рисунок"] == 80.0
