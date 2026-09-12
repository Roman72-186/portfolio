"""Напоминание куратору о дне рождения ученика (12.09.2026).

exam_scheduler._run_birthday_check — за 7 дней и ближе, один раз в год
(birthday_reminder_sent_year), только ученикам с назначенным куратором.
Плюс сам экран /cabinet/staff/notifications, куда падает уведомление.
"""
from datetime import date, timedelta

from app.models.notification import Notification
from app.services.exam_scheduler import _run_birthday_check, _upcoming_birthday
from app.services.tz import today_msk


def _birth_date_in(days: int):
    """Дата рождения так, чтобы ближайший день рождения был через `days` дней
    (год рождения не важен для логики — берём произвольный, 2010)."""
    target = today_msk() + timedelta(days=days)
    return target.replace(year=2010)


def test_flags_birthday_within_week(db, user_factory):
    curator = user_factory(vk_id=700_001, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=700_002, name="Аня Иванова")
    student.curator_id = curator.id
    student.birth_date = _birth_date_in(5)
    db.commit()

    _run_birthday_check()

    notif = db.query(Notification).filter(Notification.user_id == curator.id).first()
    assert notif is not None
    assert "Аня Иванова" in notif.title

    db.refresh(student)
    # Год самого дня рождения, не обязательно today.year — см.
    # test_does_not_double_notify_across_year_boundary ниже для случая,
    # когда они расходятся (дата рождения в первую неделю января).
    assert student.birthday_reminder_sent_year == (today_msk() + timedelta(days=5)).year


def test_ignores_birthday_far_away(db, user_factory):
    curator = user_factory(vk_id=700_003, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=700_004, name="Пётр Сидоров")
    student.curator_id = curator.id
    student.birth_date = _birth_date_in(20)
    db.commit()

    _run_birthday_check()

    assert db.query(Notification).filter(Notification.user_id == curator.id).count() == 0


def test_notifies_same_day(db, user_factory):
    curator = user_factory(vk_id=700_005, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=700_006, name="Оля Кузнецова")
    student.curator_id = curator.id
    student.birth_date = _birth_date_in(0)
    db.commit()

    _run_birthday_check()

    notif = db.query(Notification).filter(Notification.user_id == curator.id).first()
    assert notif is not None
    assert "сегодня" in notif.text


def test_does_not_repeat_within_same_year(db, user_factory):
    curator = user_factory(vk_id=700_007, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=700_008, name="Игорь Смирнов")
    student.curator_id = curator.id
    student.birth_date = _birth_date_in(3)
    student.birthday_reminder_sent_year = today_msk().year
    db.commit()

    _run_birthday_check()

    assert db.query(Notification).filter(Notification.user_id == curator.id).count() == 0


def test_skips_student_without_curator(db, user_factory):
    student = user_factory(vk_id=700_009, name="Без куратора")
    student.birth_date = _birth_date_in(2)
    db.commit()

    _run_birthday_check()  # не должно упасть и некому слать

    assert db.query(Notification).count() == 0


def test_skips_student_without_birth_date(db, user_factory):
    curator = user_factory(vk_id=700_010, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=700_011, name="Без даты")
    student.curator_id = curator.id
    db.commit()

    _run_birthday_check()

    assert db.query(Notification).filter(Notification.user_id == curator.id).count() == 0


# ── _upcoming_birthday: граница года ────────────────────────────────────────

def test_upcoming_birthday_this_year_not_yet_passed():
    assert _upcoming_birthday(date(2010, 9, 20), date(2026, 9, 12)) == date(2026, 9, 20)


def test_upcoming_birthday_already_passed_rolls_to_next_year():
    assert _upcoming_birthday(date(2010, 9, 20), date(2026, 9, 21)) == date(2027, 9, 20)


def test_upcoming_birthday_today_is_the_day():
    assert _upcoming_birthday(date(2010, 9, 12), date(2026, 9, 12)) == date(2026, 9, 12)


def test_upcoming_birthday_crosses_new_year():
    # 3 января ещё не наступило в году today (2026-12-27) — ближайший день
    # рождения приходится на следующий календарный год (2027).
    assert _upcoming_birthday(date(2010, 1, 3), date(2026, 12, 27)) == date(2027, 1, 3)


def test_upcoming_birthday_leap_day_in_non_leap_year():
    assert _upcoming_birthday(date(2012, 2, 29), date(2026, 2, 1)) == date(2026, 2, 28)


def test_does_not_double_notify_across_year_boundary(db, user_factory, monkeypatch):
    """Прецедент из ревью: день рождения 3 января, проверка идёт и 27
    декабря (days_left=7, today.year=2026), и 1 января (days_left=2,
    today.year=2027 уже сменился) — раньше это слало напоминание дважды."""
    import app.services.exam_scheduler as scheduler_module

    curator = user_factory(vk_id=700_015, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=700_016, name="Январский Именинник")
    student.curator_id = curator.id
    student.birth_date = date(2010, 1, 3)
    db.commit()

    monkeypatch.setattr(scheduler_module, "today_msk", lambda: date(2026, 12, 27))
    _run_birthday_check()

    monkeypatch.setattr(scheduler_module, "today_msk", lambda: date(2027, 1, 1))
    _run_birthday_check()

    assert db.query(Notification).filter(Notification.user_id == curator.id).count() == 1


# ── Экран уведомлений персонала ──────────────────────────────────────────────

def test_curator_sees_own_notification(db, client, session_factory, user_factory):
    curator = user_factory(vk_id=700_012, name="Куратор", role_name="куратор")
    n = Notification(user_id=curator.id, title="День рождения — Тест", text="через 5 дн., 20.09.")
    db.add(n)
    db.commit()
    client.cookies.set("session_id", session_factory(curator).id)

    resp = client.get("/cabinet/staff/notifications")

    assert resp.status_code == 200
    assert "День рождения — Тест" in resp.text

    db.refresh(n)
    assert n.is_read is True


def test_student_notification_not_visible_to_other_curator(db, client, session_factory, user_factory):
    curator_a = user_factory(vk_id=700_013, name="Куратор А", role_name="куратор")
    curator_b = user_factory(vk_id=700_014, name="Куратор Б", role_name="куратор")
    n = Notification(user_id=curator_a.id, title="День рождения — Чужой ученик")
    db.add(n)
    db.commit()
    client.cookies.set("session_id", session_factory(curator_b).id)

    resp = client.get("/cabinet/staff/notifications")

    assert resp.status_code == 200
    assert "Чужой ученик" not in resp.text


# ── Колокольчик в base.html: ссылка «Все уведомления» по роли ──────────────

def test_bell_footer_links_staff_to_their_own_screen(db, client, session_factory, user_factory):
    curator = user_factory(vk_id=700_017, name="Куратор", role_name="куратор")
    client.cookies.set("session_id", session_factory(curator).id)

    resp = client.get("/cabinet/staff/notifications")

    assert resp.status_code == 200
    assert 'href="/cabinet/staff/notifications"' in resp.text
    assert 'href="/cabinet/notifications"' not in resp.text


def test_bell_footer_links_student_to_student_screen(auth_client):
    client, _student = auth_client

    resp = client.get("/cabinet/personal")

    assert resp.status_code == 200
    assert 'href="/cabinet/notifications"' in resp.text
    assert 'href="/cabinet/staff/notifications"' not in resp.text
