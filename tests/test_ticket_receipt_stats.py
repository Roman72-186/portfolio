"""Tests for period_stats.get_ticket_receipt_stats — «кто какой билет получил»."""
from datetime import datetime, timedelta, timezone, date

from app.models.feature_period import FeaturePeriod
from app.models.mock_exam_attempt import MockExamAttempt
from app.services.period_stats import MOCK_STATS_START, get_ticket_receipt_stats
from app.services.tz import MSK_TZ


def _make_attempt(db, *, user_id, ticket_title, subject="Рисунок",
                  ticket_id=1, started_at=None, completed=False):
    a = MockExamAttempt(
        user_id=user_id,
        subject=subject,
        ticket_id=ticket_id,
        ticket_title=ticket_title,
        started_at=started_at or datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc) if completed else None,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    if started_at is not None:
        a.started_at = started_at   # обойти default=now
        db.commit()
    return a


def _make_mock_period(db, admin_id, days_back=7, days_forward=7):
    today = date.today()
    p = FeaturePeriod(
        feature="mock_exam",
        start_date=today - timedelta(days=days_back),
        end_date=today + timedelta(days=days_forward),
        is_active=True,
        created_by_id=admin_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_empty_returns_applicable_no_receipts(db, user_factory):
    res = get_ticket_receipt_stats(db)
    assert res["applicable"] is True
    assert res["total_receipts"] == 0
    assert res["by_ticket"] == []
    assert res["receipts"] == []


def test_counts_distinct_students_not_attempts(db, user_factory):
    """Один ученик с двумя попытками одного билета → 1 ученик, 2 выдачи."""
    s1 = user_factory(vk_id=1, role_name="ученик")
    s2 = user_factory(vk_id=2, role_name="ученик")
    _make_attempt(db, user_id=s1.id, ticket_title="Билет А", ticket_id=10)
    _make_attempt(db, user_id=s1.id, ticket_title="Билет А", ticket_id=10)
    _make_attempt(db, user_id=s2.id, ticket_title="Билет А", ticket_id=10)

    res = get_ticket_receipt_stats(db)
    assert res["total_students"] == 2
    assert res["total_receipts"] == 3
    assert len(res["by_ticket"]) == 1
    t = res["by_ticket"][0]
    assert t["ticket_title"] == "Билет А"
    assert t["student_count"] == 2
    assert t["attempt_count"] == 3
    assert t["deleted"] is False


def test_sorted_by_student_count_desc(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    s2 = user_factory(vk_id=2, role_name="ученик")
    _make_attempt(db, user_id=s1.id, ticket_title="Редкий", ticket_id=11)
    _make_attempt(db, user_id=s1.id, ticket_title="Частый", ticket_id=12)
    _make_attempt(db, user_id=s2.id, ticket_title="Частый", ticket_id=12)

    res = get_ticket_receipt_stats(db)
    titles = [t["ticket_title"] for t in res["by_ticket"]]
    assert titles == ["Частый", "Редкий"]


def test_deleted_ticket_grouped_by_title(db, user_factory):
    """ticket_id=None (удалён) — два разных названия не схлопываются в один бакет."""
    s1 = user_factory(vk_id=1, role_name="ученик")
    _make_attempt(db, user_id=s1.id, ticket_title="Удалён 1", ticket_id=None)
    _make_attempt(db, user_id=s1.id, ticket_title="Удалён 2", ticket_id=None)

    res = get_ticket_receipt_stats(db)
    assert len(res["by_ticket"]) == 2
    assert all(t["deleted"] for t in res["by_ticket"])


def test_started_at_converted_to_msk(db, user_factory):
    """started_at (UTC) отдаётся в шаблон уже в MSK."""
    s1 = user_factory(vk_id=1, role_name="ученик")
    utc_dt = datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc)   # 12:00 MSK
    _make_attempt(db, user_id=s1.id, ticket_title="Билет", ticket_id=10, started_at=utc_dt)

    res = get_ticket_receipt_stats(db)
    started = res["receipts"][0]["started_at"]
    assert started.utcoffset() == MSK_TZ.utcoffset(started)
    assert started.hour == 12


def test_not_applicable_for_portfolio_feature(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    _make_attempt(db, user_id=s1.id, ticket_title="Билет", ticket_id=10)

    res = get_ticket_receipt_stats(db, feature="portfolio_upload")
    assert res["applicable"] is False
    assert res["receipts"] == []


def test_aggregate_not_capped_by_detail_limit(db, user_factory):
    """Агрегат «сколько учеников получили» точный даже при >500 выдач,
    хотя детальный список капается 500-ю записями."""
    s1 = user_factory(vk_id=1, role_name="ученик")
    s2 = user_factory(vk_id=2, role_name="ученик")
    base = datetime(2026, 6, 14, tzinfo=timezone.utc)   # после floor 13.06.2026
    # 600 выдач одного билета двумя учениками
    attempts = [
        MockExamAttempt(
            user_id=(s1.id if i % 2 == 0 else s2.id),
            subject="Рисунок",
            ticket_id=10,
            ticket_title="Массовый билет",
            started_at=base + timedelta(minutes=i),
        )
        for i in range(600)
    ]
    db.add_all(attempts)
    db.commit()

    res = get_ticket_receipt_stats(db)
    assert res["total_receipts"] == 600          # не 500
    assert res["total_students"] == 2
    assert len(res["receipts"]) == 500           # детальный список — капается
    assert len(res["by_ticket"]) == 1
    assert res["by_ticket"][0]["attempt_count"] == 600
    assert res["by_ticket"][0]["student_count"] == 2


def test_floor_excludes_attempts_before_13_06_2026(db, user_factory):
    """Выдачи билетов до 13.06.2026 не учитываются (раньше не было ОС по пробникам)."""
    s1 = user_factory(vk_id=1, role_name="ученик")
    before = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)   # до floor
    after = datetime(2026, 6, 13, 10, 0, tzinfo=timezone.utc)    # ровно с floor
    _make_attempt(db, user_id=s1.id, ticket_title="Старый", ticket_id=10, started_at=before)
    _make_attempt(db, user_id=s1.id, ticket_title="Новый", ticket_id=11, started_at=after)

    res = get_ticket_receipt_stats(db)
    assert res["total_receipts"] == 1
    titles = {t["ticket_title"] for t in res["by_ticket"]}
    assert titles == {"Новый"}
    assert MOCK_STATS_START == date(2026, 6, 13)


def test_period_window_filters_attempts(db, user_factory):
    """Попытка вне окна периода не попадает в выборку."""
    admin = user_factory(vk_id=999, role_name="суперадмин")
    s1 = user_factory(vk_id=1, role_name="ученик")
    period = _make_mock_period(db, admin.id)

    inside = datetime.now(timezone.utc)
    outside = datetime.now(timezone.utc) - timedelta(days=90)
    _make_attempt(db, user_id=s1.id, ticket_title="Внутри", ticket_id=10, started_at=inside)
    _make_attempt(db, user_id=s1.id, ticket_title="Снаружи", ticket_id=11, started_at=outside)

    res = get_ticket_receipt_stats(db, period_id=period.id)
    titles = {t["ticket_title"] for t in res["by_ticket"]}
    assert titles == {"Внутри"}
