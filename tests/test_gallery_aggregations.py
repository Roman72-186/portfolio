"""Aggregation correctness for app/api/gallery.py (Фаза 6, п.4 — не было прямых тестов).

Существующие тесты (test_student_behavior.py) проверяют доступ/рендер (200,
группировку по месяцам, IDOR на /gallery/thumb), но не то, что счётчики
before/after/mock/retake и /history считают правильные числа и не текут между
пользователями.
"""
from datetime import datetime, timezone

from app.models.work import (
    Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM, WORK_TYPE_RETAKE,
)
from app.models.upload_log import UploadLog


def _work(user_id, work_type, *, status="success", month="май", year=2026, filename="a.jpg"):
    return Work(
        user_id=user_id, work_type=work_type, month=month, year=year,
        filename=filename, s3_url=f"https://s3.example/{filename}", status=status,
        created_at=datetime.now(timezone.utc),
    )


def test_gallery_counts_match_work_rows_per_type(auth_client, db):
    """before/after/mock/retake считают ровно то, что реально в БД (group.total по месяцу)."""
    client, user = auth_client
    db.add_all([
        _work(user.id, WORK_TYPE_BEFORE, filename="b1.jpg"),
        _work(user.id, WORK_TYPE_BEFORE, filename="b2.jpg"),
        _work(user.id, WORK_TYPE_AFTER, filename="a1.jpg"),
        _work(user.id, WORK_TYPE_MOCK_EXAM, filename="m1.jpg"),
        _work(user.id, WORK_TYPE_RETAKE, filename="r1.jpg"),
    ])
    db.commit()

    resp = client.get("/cabinet/gallery")
    assert resp.status_code == 200
    text = resp.text
    # Все работы одного типа в одном месяце группируются в один блок "N фото".
    assert '<span class="month-count">2 фото</span>' in text  # before: b1+b2
    assert text.count('<span class="month-count">1 фото</span>') == 3  # after, mock, retake — по 1


def test_gallery_excludes_non_success_and_other_users_works(auth_client, db, user_factory):
    """Незавершённые (status != success) и чужие Work не должны попадать в счётчики."""
    client, user = auth_client
    other = user_factory(vk_id=555_444, name="Other Student")

    db.add_all([
        _work(user.id, WORK_TYPE_BEFORE, filename="mine.jpg"),
        _work(user.id, WORK_TYPE_BEFORE, status="pending", filename="pending.jpg"),
        _work(user.id, WORK_TYPE_BEFORE, status="failed", filename="failed.jpg"),
        _work(other.id, WORK_TYPE_BEFORE, filename="others.jpg"),
    ])
    db.commit()

    resp = client.get("/cabinet/gallery")
    assert resp.status_code == 200
    text = resp.text
    # Ровно одна успешная своя работа — группа должна показывать "1 фото", не 4.
    assert '<span class="month-count">1 фото</span>' in text
    assert "others.jpg" not in text
    assert "pending.jpg" not in text
    assert "failed.jpg" not in text


def test_history_totals_count_success_and_failed_separately(auth_client, db):
    """/cabinet/history: total_success/total_failed/total_photos считаются раздельно.

    2 успешных лога (photo_count 3+2=5) + 1 неудачный → total_success=2,
    total_failed=1, total_photos=5 (photo_count неудачного лога не учитывается).
    """
    client, user = auth_client
    db.add_all([
        UploadLog(user_id=user.id, student_name=user.name, tariff=user.tariff, month="май",
                  photo_type=WORK_TYPE_BEFORE, photo_count=3, status="success"),
        UploadLog(user_id=user.id, student_name=user.name, tariff=user.tariff, month="май",
                  photo_type=WORK_TYPE_AFTER, photo_count=2, status="success"),
        UploadLog(user_id=user.id, student_name=user.name, tariff=user.tariff, month="май",
                  photo_type=WORK_TYPE_BEFORE, photo_count=1, status="failed"),
    ])
    db.commit()

    resp = client.get("/cabinet/history")
    assert resp.status_code == 200
    text = resp.text
    assert 'Всего фото: <span style="color:var(--blue)">5</span>' in text
    assert 'Успешно: <span style="color:var(--success)">2</span>' in text
    assert 'Ошибок: <span style="color:var(--error)">1</span>' in text
