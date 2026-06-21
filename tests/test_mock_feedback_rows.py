"""Tests for period_stats.get_mock_feedback_rows — таблица пробников + ОС."""
from datetime import datetime, timedelta, timezone, date

from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.feedback import ROLE_CURATOR, ROLE_STUDENT
from app.services.period_stats import (
    MOCK_STATS_START,
    MOCK_SCORE_RANGES,
    get_mock_feedback_rows,
    get_mock_score_stats,
    get_mock_subject_status,
)
from app.services.tz import MSK_TZ


def _make_final_mock(db, *, user_id, subject="Рисунок", score=None,
                     scored_by_id=None, cycle_id=None, created_at=None):
    w = Work(
        user_id=user_id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="июнь",
        year=2026,
        filename=f"mock-{user_id}.jpg",
        subject=subject,
        score=score,
        scored_by_id=scored_by_id,
        cycle_id=cycle_id,
        status="success",
        is_final=True,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    if created_at is not None:
        w.created_at = created_at
        db.commit()
    return w


def _make_cycle_with_ticket(db, *, user_id, admin_id, subject="Рисунок", title="Билет про натюрморт"):
    assignment = ExamAssignment(title="Задание", subject=subject, created_by_id=admin_id, status="published")
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    ticket = ExamTicket(
        assignment_id=assignment.id, ticket_number=1, title=title,
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 30),
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    cycle = ExamCycle(user_id=user_id, subject=subject, ticket_id=ticket.id, started_at=date(2026, 6, 10))
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle, ticket


def _add_feedback(db, *, work_id, curator_id, messages):
    """messages: list of (sender_id, role, text_or_None, photo_url_or_None)."""
    fb = Feedback(work_id=work_id, curator_id=curator_id)
    db.add(fb)
    db.commit()
    db.refresh(fb)
    for i, (sid, role, text, photo) in enumerate(messages):
        m = FeedbackMessage(
            feedback_id=fb.id, sender_id=sid, sender_role=role,
            text=text, photo_s3_url=photo,
        )
        db.add(m)
        db.commit()
        db.refresh(m)
        m.created_at = datetime(2026, 6, 12, 8, i, tzinfo=timezone.utc)
        db.commit()
    return fb


def test_empty_applicable_no_rows(db, user_factory):
    res = get_mock_feedback_rows(db)
    assert res["applicable"] is True
    assert res["total"] == 0
    assert res["rows"] == []
    assert res["avg_score"] is None


def test_submission_without_feedback_included(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    _make_final_mock(db, user_id=s1.id, subject="Композиция")

    res = get_mock_feedback_rows(db)
    assert res["total"] == 1
    assert res["with_feedback"] == 0
    assert len(res["rows"]) == 1
    row = res["rows"][0]
    assert row["has_feedback"] is False
    assert row["curator_name"] is None
    assert row["feedback_at"] is None
    assert row["feedback_text"] == ""
    assert row["ticket_title"] is None


def test_row_has_ticket_score_curator_and_feedback(db, user_factory):
    admin = user_factory(vk_id=999, role_name="суперадмин")
    curator = user_factory(vk_id=500, role_name="куратор")
    curator.first_name = "Анна"
    curator.last_name = "Кур"
    s1 = user_factory(vk_id=1, role_name="ученик")
    db.commit()

    cycle, ticket = _make_cycle_with_ticket(db, user_id=s1.id, admin_id=admin.id)
    w = _make_final_mock(db, user_id=s1.id, score=72, scored_by_id=curator.id, cycle_id=cycle.id)
    _add_feedback(db, work_id=w.id, curator_id=curator.id, messages=[
        (curator.id, ROLE_CURATOR, "Хорошая работа, поправь тон", None),
        (s1.id, ROLE_STUDENT, "Спасибо!", None),
    ])

    res = get_mock_feedback_rows(db)
    assert res["with_feedback"] == 1
    assert res["avg_score"] == 72.0
    row = res["rows"][0]
    assert row["ticket_title"] == "Билет про натюрморт"
    assert row["score"] == 72.0
    assert row["curator_name"] == "Кур Анна"
    assert row["has_feedback"] is True
    # время ОС = первое staff-сообщение (08:00 UTC → 11:00 MSK)
    assert row["feedback_at"].hour == 11
    assert "Хорошая работа" in row["feedback_text"]
    assert "Спасибо!" in row["feedback_text"]


def test_photo_only_message_marked(db, user_factory):
    admin = user_factory(vk_id=999, role_name="суперадмин")
    curator = user_factory(vk_id=500, role_name="куратор")
    s1 = user_factory(vk_id=1, role_name="ученик")
    db.commit()
    w = _make_final_mock(db, user_id=s1.id, score=60, scored_by_id=curator.id)
    _add_feedback(db, work_id=w.id, curator_id=curator.id, messages=[
        (curator.id, ROLE_CURATOR, None, "https://s3/photo.jpg"),
    ])

    res = get_mock_feedback_rows(db)
    row = res["rows"][0]
    assert "[фото]" in row["feedback_text"]
    assert row["feedback_at"] is not None   # время ОС есть даже у фото-сообщения


def test_curator_fallback_to_scored_by(db, user_factory):
    """Без Feedback куратор = кто выставил балл (scored_by_id)."""
    curator = user_factory(vk_id=500, role_name="куратор")
    curator.first_name = "Олег"
    curator.last_name = "Балл"
    s1 = user_factory(vk_id=1, role_name="ученик")
    db.commit()
    _make_final_mock(db, user_id=s1.id, score=88, scored_by_id=curator.id)

    res = get_mock_feedback_rows(db)
    row = res["rows"][0]
    assert row["curator_name"] == "Балл Олег"
    assert row["has_feedback"] is False


def test_submitted_at_in_msk(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    utc_dt = datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc)   # 12:00 MSK
    _make_final_mock(db, user_id=s1.id, created_at=utc_dt)

    res = get_mock_feedback_rows(db)
    submitted = res["rows"][0]["submitted_at"]
    assert submitted.hour == 12
    assert submitted.utcoffset() == MSK_TZ.utcoffset(submitted)


def test_not_applicable_for_portfolio_feature(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    _make_final_mock(db, user_id=s1.id)
    res = get_mock_feedback_rows(db, feature="portfolio_upload")
    assert res["applicable"] is False
    assert res["rows"] == []


def test_avg_and_count_not_capped_by_detail_limit(db, user_factory):
    """avg_score и total — из отдельных запросов без лимита 500."""
    s1 = user_factory(vk_id=1, role_name="ученик")
    base = datetime(2026, 6, 14, tzinfo=timezone.utc)   # после floor 13.06.2026
    for i in range(600):
        _make_final_mock(db, user_id=s1.id, score=50, created_at=base + timedelta(minutes=i))

    res = get_mock_feedback_rows(db)
    assert res["total"] == 600          # не 500
    assert res["avg_score"] == 50.0
    assert len(res["rows"]) == 500      # детальный список капается по умолчанию

    # limit=None — выгрузка всей таблицы (для Excel)
    res_all = get_mock_feedback_rows(db, limit=None)
    assert len(res_all["rows"]) == 600


def test_floor_excludes_submissions_before_13_06_2026(db, user_factory):
    """Сдачи пробников до 13.06.2026 не учитываются в таблице ОС."""
    s1 = user_factory(vk_id=1, role_name="ученик")
    before = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
    after = datetime(2026, 6, 13, 10, 0, tzinfo=timezone.utc)
    _make_final_mock(db, user_id=s1.id, score=40, created_at=before)
    _make_final_mock(db, user_id=s1.id, score=90, created_at=after)

    res = get_mock_feedback_rows(db)
    assert res["total"] == 1
    assert res["avg_score"] == 90.0
    assert MOCK_STATS_START == date(2026, 6, 13)


# ─── get_mock_subject_status ─────────────────────────────────────────────────

def test_subject_status_returns_both_keys(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    _make_final_mock(db, user_id=s1.id, subject="Рисунок")   # сдал только Рисунок

    res = get_mock_subject_status(db)
    assert res["applicable"] is True
    # ученик есть в статусе и в «не сдали» (Композицию не сдал)
    all_entries = [e for lst in res["by_tariff_mock_status"].values() for e in lst]
    assert any(e["student_id"] == s1.id and e["risunok"] and not e["kompoziciya"] for e in all_entries)
    ns_entries = [e for lst in res["not_submitted_by_tariff"].values() for e in lst]
    assert any(e["student_id"] == s1.id for e in ns_entries)


def test_subject_status_floor_excludes_old_submissions(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    before = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
    _make_final_mock(db, user_id=s1.id, subject="Рисунок", created_at=before)

    res = get_mock_subject_status(db)
    all_entries = [e for lst in res["by_tariff_mock_status"].values() for e in lst]
    me = next(e for e in all_entries if e["student_id"] == s1.id)
    assert me["risunok"] is False   # сдача до floor не засчитана


def test_subject_status_not_applicable_for_portfolio(db, user_factory):
    res = get_mock_subject_status(db, feature="portfolio_upload")
    assert res["applicable"] is False
    assert res["by_tariff_mock_status"] == {}


# ─── get_mock_score_stats ─────────────────────────────────────────────────

def test_score_stats_empty(db, user_factory):
    res = get_mock_score_stats(db)
    assert res["applicable"] is True
    assert set(res["by_subject"].keys()) == {"Рисунок", "Композиция"}
    for data in res["by_subject"].values():
        assert data["total"] == 0
        assert [r["count"] for r in data["ranges"]] == [0, 0, 0, 0]
        assert [r["label"] for r in data["ranges"]] == [f"{lo}–{hi}" for lo, hi in MOCK_SCORE_RANGES]


def test_score_stats_buckets_by_range_and_subject(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    s2 = user_factory(vk_id=2, role_name="ученик")
    s3 = user_factory(vk_id=3, role_name="ученик")
    s4 = user_factory(vk_id=4, role_name="ученик")

    # Рисунок: один балл в каждом из диапазонов 0-50, 55-65, 70-75, 80-85
    _make_final_mock(db, user_id=s1.id, subject="Рисунок", score=50)
    _make_final_mock(db, user_id=s2.id, subject="Рисунок", score=58)
    _make_final_mock(db, user_id=s3.id, subject="Рисунок", score=72)
    _make_final_mock(db, user_id=s4.id, subject="Рисунок", score=85)
    # Композиция: только один балл, попадающий в 70-75
    _make_final_mock(db, user_id=s1.id, subject="Композиция", score=74)
    # Балл вне всех диапазонов — учтён в total, но не в ranges
    _make_final_mock(db, user_id=s2.id, subject="Композиция", score=67)

    res = get_mock_score_stats(db)
    risunok = res["by_subject"]["Рисунок"]
    assert risunok["total"] == 4
    assert [r["count"] for r in risunok["ranges"]] == [1, 1, 1, 1]

    kompoziciya = res["by_subject"]["Композиция"]
    assert kompoziciya["total"] == 2
    assert [r["count"] for r in kompoziciya["ranges"]] == [0, 0, 1, 0]


def test_score_stats_not_applicable_for_portfolio(db, user_factory):
    res = get_mock_score_stats(db, feature="portfolio_upload")
    assert res["applicable"] is False
    assert res["by_subject"] == {}


def test_score_stats_floor_excludes_old_submissions(db, user_factory):
    s1 = user_factory(vk_id=1, role_name="ученик")
    before = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
    _make_final_mock(db, user_id=s1.id, subject="Рисунок", score=50, created_at=before)

    res = get_mock_score_stats(db)
    assert res["by_subject"]["Рисунок"]["total"] == 0


def test_score_stats_counts_distinct_students_not_works(db, user_factory):
    """Один ученик с несколькими финальными сдачами считается один раз в total,
    но попадает в каждый диапазон, куда попал хоть один из его баллов."""
    s1 = user_factory(vk_id=1, role_name="ученик")
    _make_final_mock(db, user_id=s1.id, subject="Рисунок", score=50)
    _make_final_mock(db, user_id=s1.id, subject="Рисунок", score=72)

    res = get_mock_score_stats(db)
    risunok = res["by_subject"]["Рисунок"]
    assert risunok["total"] == 1
    assert [r["count"] for r in risunok["ranges"]] == [1, 0, 1, 0]
