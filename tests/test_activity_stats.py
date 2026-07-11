"""Тесты страницы «Статистика активности» (/cabinet/superadmin/activity)
и сервиса app/services/activity_stats.py."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditLog
from app.models.curator_report import CuratorReport
from app.models.exam_cycle import ExamCycle
from app.models.notification import Notification
from app.models.work import Work, WORK_TYPE_MOCK_EXAM


@pytest.fixture()
def superadmin_client(client, db, user_factory, session_factory):
    user = user_factory(vk_id=970001, name="Super Admin", role_name="суперадмин")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    return client, user


# ── Сервис ────────────────────────────────────────────────────────────────────

def test_fmt_duration():
    from app.services.activity_stats import fmt_duration

    assert fmt_duration(None) is None
    assert fmt_duration(300) == "5 м."
    assert fmt_duration(4500) == "1 ч. 15 м."
    assert fmt_duration(90000) == "1 д. 1 ч."


def test_login_stats_counts_and_inactive(db, user_factory):
    from app.services.activity_stats import get_login_stats

    now = datetime.now(timezone.utc)
    fresh = user_factory(vk_id=970101, name="Свежий", role_name="ученик")
    fresh.last_login_at = now - timedelta(days=1)
    stale = user_factory(vk_id=970102, name="Старый", role_name="ученик")
    stale.last_login_at = now - timedelta(days=20)
    never = user_factory(vk_id=970103, name="Никогда", role_name="ученик")
    db.commit()

    stats = get_login_stats(db)
    assert stats["total"] == 3
    assert stats["d7"] == 1
    assert stats["d30"] == 2
    inactive_names = [s["student_name"] for s in stats["inactive"]]
    assert "Никогда" in " ".join(inactive_names) or any("Никогда" in n for n in inactive_names)
    assert not any("Свежий" in n for n in inactive_names)


def test_curator_review_speed_aggregates(db, user_factory):
    from app.services.activity_stats import get_curator_review_speed

    curator = user_factory(vk_id=970201, name="Куратор Проверяющий", role_name="куратор")
    student = user_factory(vk_id=970202, name="Ученик", role_name="ученик")
    created = datetime.now(timezone.utc) - timedelta(hours=2)
    db.add(Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="июнь", year=2026,
        filename="w.jpg", status="success", score=80,
        created_at=created, scored_at=created + timedelta(hours=1),
        scored_by_id=curator.id,
    ))
    db.commit()

    rows = get_curator_review_speed(db)
    assert len(rows) == 1
    assert rows[0]["scored_count"] == 1
    assert rows[0]["avg_score"] == 80.0
    # ~1 час на проверку
    assert 3500 <= rows[0]["avg_review_seconds"] <= 3700


def test_revision_stats_pending_and_avg(db, user_factory):
    from app.services.activity_stats import get_revision_stats

    student = user_factory(vk_id=970301, name="Ревизия Ученик", role_name="ученик")
    now = datetime.now(timezone.utc)
    db.add(ExamCycle(
        user_id=student.id, subject="Рисунок", started_at=date.today(),
        revision_requested_at=now - timedelta(hours=3),
    ))
    db.add(ExamCycle(
        user_id=student.id, subject="Композиция", started_at=date.today(),
        revision_requested_at=now - timedelta(hours=5),
        revision_done_at=now - timedelta(hours=3),
    ))
    db.commit()

    stats = get_revision_stats(db)
    assert len(stats["pending"]) == 1
    assert stats["pending"][0]["subject"] == "Рисунок"
    assert stats["done_count"] == 1
    # правка заняла ~2 часа
    assert 7100 <= stats["avg_fix_seconds"] <= 7300


def test_notification_reaction(db, user_factory):
    from app.services.activity_stats import get_notification_reaction

    student = user_factory(vk_id=970401, name="Уведомляемый", role_name="ученик")
    now = datetime.now(timezone.utc)
    db.add(Notification(
        user_id=student.id, title="Прочитанное",
        is_read=True, created_at=now - timedelta(minutes=30), read_at=now,
    ))
    db.add(Notification(user_id=student.id, title="Непрочитанное", is_read=False))
    db.commit()

    stats = get_notification_reaction(db)
    assert stats["read_count"] == 1
    assert stats["unread_count"] == 1
    assert 1700 <= stats["avg_reaction_seconds"] <= 1900  # ~30 минут


# ── Роут ──────────────────────────────────────────────────────────────────────

def test_activity_page_renders_with_data(superadmin_client, db, user_factory):
    client, sa = superadmin_client
    curator = user_factory(vk_id=970501, name="Куратор Быстрый", role_name="куратор")
    student = user_factory(vk_id=970502, name="Ученик Активный", role_name="ученик")
    now = datetime.now(timezone.utc)
    student.last_login_at = now - timedelta(days=1)

    created = now - timedelta(hours=4)
    db.add(Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="июль", year=2026,
        filename="w.jpg", status="success", score=75,
        created_at=created, scored_at=created + timedelta(hours=2),
        scored_by_id=curator.id,
    ))
    db.add(ExamCycle(
        user_id=student.id, subject="Рисунок", started_at=date.today(),
        revision_requested_at=now - timedelta(hours=1),
    ))
    db.add(AuditLog(
        action="curator_assign", performed_by_id=sa.id,
        target_user_id=student.id, details="curator: — → 5",
    ))
    db.add(AuditLog(
        action="tariff_change", performed_by_id=student.id,
        target_user_id=student.id, details="tariff: УВЕРЕННЫЙ → МАКСИМУМ",
    ))
    db.add(CuratorReport(
        curator_id=curator.id, video_url="https://s3.example/report.mp4",
        viewed_at=now, viewed_by_id=sa.id,
    ))
    db.commit()

    resp = client.get("/cabinet/superadmin/activity")
    assert resp.status_code == 200
    assert "Статистика активности" in resp.text
    assert "Скорость проверки работ" in resp.text
    assert "Куратор Быстрый" in resp.text
    assert "Возвраты ОС на правку" in resp.text
    assert "Ученик Активный" in resp.text
    assert "Смена куратора" in resp.text
    assert "Смена тарифа" in resp.text
    assert "curator: — → 5" in resp.text


def test_activity_page_forbidden_for_student(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/superadmin/activity")
    assert resp.status_code == 403


def test_activity_page_renders_empty(superadmin_client):
    client, _ = superadmin_client
    resp = client.get("/cabinet/superadmin/activity")
    assert resp.status_code == 200
    assert "Оценённых работ пока нет" in resp.text
    assert "Сейчас ни один цикл не висит на правке" in resp.text
