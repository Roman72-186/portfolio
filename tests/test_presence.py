"""«Был на платформе» и время на платформе (Александрия 22.10.2026).

Дата последнего входа застывала: сессия продлевается сама, и ежедневный
ученик неделями «не входил». Теперь отметку ставит пинг открытой вкладки
(`POST /presence`), он же копит время по дням (`services/presence.py`).
"""
from datetime import date, datetime, timedelta, timezone

from app.models.session import Session as DbSession
from app.models.user import UserActivityDay
from app.services.activity_stats import get_login_stats
from app.services.presence import record_presence, seconds_on_platform

T0 = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)  # 12:00 МСК


def _days(db, user_id):
    return {
        row.day: row.seconds
        for row in db.query(UserActivityDay).filter(UserActivityDay.user_id == user_id)
    }


def test_time_is_gap_between_pings_and_long_gap_is_new_visit(db, user_factory):
    student = user_factory(vk_id=830_001)

    record_presence(db, student.id, T0)
    record_presence(db, student.id, T0 + timedelta(seconds=60))
    record_presence(db, student.id, T0 + timedelta(seconds=120))
    # Перерыв на час — новый заход, час не засчитывается.
    record_presence(db, student.id, T0 + timedelta(hours=1))
    record_presence(db, student.id, T0 + timedelta(hours=1, seconds=60))

    assert _days(db, student.id) == {date(2026, 10, 26): 180}
    db.refresh(student)
    assert student.last_seen_at.replace(tzinfo=timezone.utc) == T0 + timedelta(hours=1, seconds=60)


def test_two_open_tabs_do_not_double_the_time(db, user_factory):
    student = user_factory(vk_id=830_002)

    # Две вкладки по пингу в минуту со сдвигом в 30 с.
    for second in range(0, 181, 30):
        record_presence(db, student.id, T0 + timedelta(seconds=second))

    assert _days(db, student.id) == {date(2026, 10, 26): 180}


def test_day_is_moscow_day(db, user_factory):
    student = user_factory(vk_id=830_003)
    evening_utc = datetime(2026, 10, 26, 20, 59, 30, tzinfo=timezone.utc)  # 23:59:30 МСК

    record_presence(db, student.id, evening_utc)
    record_presence(db, student.id, evening_utc + timedelta(seconds=60))  # 00:00:30 МСК

    assert _days(db, student.id) == {date(2026, 10, 26): 0, date(2026, 10, 27): 60}
    assert seconds_on_platform(db, [student.id], since=date(2026, 10, 27)) == {student.id: 60}


def test_presence_route_counts_student(auth_client, db):
    client, student = auth_client

    resp = client.post("/presence")

    assert resp.status_code == 204
    db.refresh(student)
    assert student.last_seen_at is not None
    assert db.query(UserActivityDay).filter(UserActivityDay.user_id == student.id).count() == 1


def test_presence_route_ignores_staff_and_impersonation(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=830_010, role_name="куратор")
    client.cookies.set("session_id", session_factory(curator).id)
    assert client.post("/presence").status_code == 204

    admin = user_factory(vk_id=830_011, role_name="суперадмин")
    student = user_factory(vk_id=830_012)
    view = DbSession(
        user_id=student.id, impersonated_by_id=admin.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(view)
    db.commit()
    client.cookies.set("session_id", view.id)
    assert client.post("/presence").status_code == 204

    assert db.query(UserActivityDay).count() == 0
    for user in (curator, student):
        db.refresh(user)
        assert user.last_seen_at is None


def test_presence_open_with_expired_access(auth_client, db):
    """Закрытый кабинет: ученик на «Личной информации» тоже на платформе,
    пинг не получает 403 раз в минуту."""
    client, student = auth_client
    student.access_until = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    assert client.post("/presence").status_code == 204


def test_login_stats_use_last_seen(db, user_factory):
    """Вход был три недели назад, на платформе — вчера: ученик «заходил»."""
    student = user_factory(vk_id=830_020)
    now = datetime.now(timezone.utc)
    student.last_login_at = now - timedelta(days=21)
    student.last_seen_at = now - timedelta(days=1)
    db.commit()

    stats = get_login_stats(db)

    assert stats["d7"] == 1
    assert stats["inactive"] == []


def test_script_only_for_student(auth_client, client, user_factory, session_factory):
    student_client, _ = auth_client
    assert "/static/js/presence.js" in student_client.get("/cabinet/personal").text

    curator = user_factory(vk_id=830_030, role_name="куратор")
    client.cookies.set("session_id", session_factory(curator).id)
    page = client.get("/cabinet", follow_redirects=True).text
    assert "/static/js/presence.js" not in page
