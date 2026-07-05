from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.cases import build_case_rows, find_case_growths_for_works, has_case_growth


def _item(
    *,
    id: int,
    user_id: int = 1,
    subject: str = "Рисунок",
    score: int = 0,
    month: str = "июнь",
    created_at: datetime | None = None,
):
    return SimpleNamespace(
        id=id,
        user_id=user_id,
        work_type=WORK_TYPE_MOCK_EXAM,
        subject=subject,
        score=score,
        month=month,
        year=2026,
        created_at=created_at or datetime(2026, 6, id, tzinfo=timezone.utc),
        scored_at=None,
    )


def test_case_growth_requires_adjacent_jump_of_ten_points():
    works = [
        _item(id=1, score=60),
        _item(id=2, score=69),
        _item(id=3, score=70),
    ]

    assert find_case_growths_for_works(works) == []
    assert has_case_growth(works) is False


def test_case_growth_counts_adjacent_jump_by_same_subject():
    works = [
        _item(id=1, subject="Рисунок", score=60),
        _item(id=2, subject="Композиция", score=90),
        _item(id=3, subject="Рисунок", score=65),
        _item(id=4, subject="Рисунок", score=75),
    ]

    cases = find_case_growths_for_works(works)

    assert len(cases) == 1
    assert cases[0].previous_work_id == 3
    assert cases[0].current_work_id == 4
    assert cases[0].growth == 10
    assert has_case_growth(works) is True


def test_build_case_rows_filters_period_by_current_work_date(db, user_factory):
    student = user_factory(vk_id=777001, name="Case Student")
    db.add_all([
        Work(
            user_id=student.id,
            work_type=WORK_TYPE_MOCK_EXAM,
            status="success",
            month="июнь",
            year=2026,
            filename="before.jpg",
            s3_url="https://cdn.test/before.jpg",
            subject="Рисунок",
            score=60,
            created_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        ),
        Work(
            user_id=student.id,
            work_type=WORK_TYPE_MOCK_EXAM,
            status="success",
            month="июль",
            year=2026,
            filename="after.jpg",
            s3_url="https://cdn.test/after.jpg",
            subject="Рисунок",
            score=72,
            created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
        ),
    ])
    db.commit()

    rows = build_case_rows(db, start_date=datetime(2026, 7, 1).date())

    assert len(rows) == 1
    assert rows[0].student_id == student.id
    assert rows[0].previous_score == 60
    assert rows[0].current_score == 72
    assert rows[0].growth == 12


def test_admin_cases_page_lists_cases_and_applies_date_filter(admin_client, db, user_factory):
    client, _admin = admin_client
    student = user_factory(vk_id=777002, name="Marketing Case")
    quiet_student = user_factory(vk_id=777003, name="No Case")
    db.add_all([
        Work(
            user_id=student.id,
            work_type=WORK_TYPE_MOCK_EXAM,
            status="success",
            month="июнь",
            year=2026,
            filename="m-before.jpg",
            s3_url="https://cdn.test/m-before.jpg",
            subject="Рисунок",
            score=60,
            created_at=datetime(2026, 6, 13, tzinfo=timezone.utc),
        ),
        Work(
            user_id=student.id,
            work_type=WORK_TYPE_MOCK_EXAM,
            status="success",
            month="июнь",
            year=2026,
            filename="m-after.jpg",
            s3_url="https://cdn.test/m-after.jpg",
            subject="Рисунок",
            score=70,
            created_at=datetime(2026, 6, 27, tzinfo=timezone.utc),
        ),
        Work(
            user_id=quiet_student.id,
            work_type=WORK_TYPE_MOCK_EXAM,
            status="success",
            month="июнь",
            year=2026,
            filename="q-before.jpg",
            subject="Рисунок",
            score=50,
            created_at=datetime(2026, 6, 13, tzinfo=timezone.utc),
        ),
        Work(
            user_id=quiet_student.id,
            work_type=WORK_TYPE_MOCK_EXAM,
            status="success",
            month="июнь",
            year=2026,
            filename="q-after.jpg",
            subject="Рисунок",
            score=59,
            created_at=datetime(2026, 6, 27, tzinfo=timezone.utc),
        ),
    ])
    db.commit()

    resp = client.get("/cabinet/cases")
    assert resp.status_code == 200
    assert "Marketing Case" in resp.text
    assert "No Case" not in resp.text
    assert "+10" in resp.text

    filtered = client.get("/cabinet/cases?start_date=2026-07-01")
    assert filtered.status_code == 200
    assert "Marketing Case" not in filtered.text
