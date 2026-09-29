"""Напоминание о дне рождения ученика (12.09.2026, адресат — ГП с 29.09.2026).

exam_scheduler._run_birthday_check — за 7 дней и ближе, один раз в год
(birthday_reminder_sent_year), каждому активному Главному преподавателю.
Куратору, суперадмину и модератору не уходит (владелец 29.09.2026:
«не куратор, а ГП»). Плюс сам экран /cabinet/staff/notifications.
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


def _count_for(db, user_id: int) -> int:
    return db.query(Notification).filter(Notification.user_id == user_id).count()


def test_flags_birthday_within_week(db, user_factory):
    chief = user_factory(vk_id=700_001, name="ГП", role_name="админ")
    student = user_factory(vk_id=700_002, name="Аня Иванова")
    student.birth_date = _birth_date_in(5)
    db.commit()

    _run_birthday_check()

    notif = db.query(Notification).filter(Notification.user_id == chief.id).first()
    assert notif is not None
    assert "Аня Иванова" in notif.title

    db.refresh(student)
    # Год самого дня рождения, не обязательно today.year — см.
    # test_does_not_double_notify_across_year_boundary ниже для случая,
    # когда они расходятся (дата рождения в первую неделю января).
    assert student.birthday_reminder_sent_year == (today_msk() + timedelta(days=5)).year


def test_goes_to_chief_teacher_not_curator(db, user_factory):
    """Главный сценарий правки 29.09.2026: куратор ученика напоминание
    больше не получает, его получает ГП."""
    chief = user_factory(vk_id=700_020, name="ГП", role_name="админ")
    curator = user_factory(vk_id=700_021, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=700_022, name="Лена Орлова")
    student.curator_id = curator.id
    student.birth_date = _birth_date_in(3)
    db.commit()

    _run_birthday_check()

    assert _count_for(db, chief.id) == 1
    assert _count_for(db, curator.id) == 0


def test_every_chief_teacher_gets_one(db, user_factory):
    chief_a = user_factory(vk_id=700_023, name="ГП А", role_name="админ")
    chief_b = user_factory(vk_id=700_024, name="ГП Б", role_name="админ")
    student = user_factory(vk_id=700_025, name="Максим Лебедев")
    student.birth_date = _birth_date_in(4)
    db.commit()

    _run_birthday_check()
    _run_birthday_check()  # повторный прогон в тот же день дублей не даёт

    assert _count_for(db, chief_a.id) == 1
    assert _count_for(db, chief_b.id) == 1


def test_superadmin_and_moderator_not_notified(db, user_factory):
    user_factory(vk_id=700_026, name="ГП", role_name="админ")
    superadmin = user_factory(vk_id=700_027, name="Суперадмин", role_name="суперадмин")
    moderator = user_factory(vk_id=700_028, name="Модератор", role_name="модератор")
    student = user_factory(vk_id=700_029, name="Вера Морозова")
    student.birth_date = _birth_date_in(1)
    db.commit()

    _run_birthday_check()

    assert _count_for(db, superadmin.id) == 0
    assert _count_for(db, moderator.id) == 0


def test_inactive_chief_teacher_not_notified(db, user_factory):
    active = user_factory(vk_id=700_030, name="ГП", role_name="админ")
    inactive = user_factory(vk_id=700_031, name="Бывший ГП", role_name="админ", is_active=False)
    student = user_factory(vk_id=700_032, name="Дима Волков")
    student.birth_date = _birth_date_in(2)
    db.commit()

    _run_birthday_check()

    assert _count_for(db, active.id) == 1
    assert _count_for(db, inactive.id) == 0


def test_no_chief_teacher_keeps_flag_unset(db, user_factory):
    """Без ГП слать некому — флаг не ставится, чтобы напоминание ушло
    первому назначенному ГП, а не потерялось."""
    student = user_factory(vk_id=700_033, name="Ждёт ГП")
    student.birth_date = _birth_date_in(2)
    db.commit()

    _run_birthday_check()

    assert db.query(Notification).count() == 0
    db.refresh(student)
    assert student.birthday_reminder_sent_year is None

    chief = user_factory(vk_id=700_034, name="ГП", role_name="админ")
    _run_birthday_check()

    assert _count_for(db, chief.id) == 1


def test_student_without_curator_is_not_skipped(db, user_factory):
    """До 29.09.2026 ученик без куратора выпадал — адресата не было.
    Теперь адресат — ГП, и куратор не нужен."""
    chief = user_factory(vk_id=700_009, name="ГП", role_name="админ")
    student = user_factory(vk_id=700_035, name="Без куратора")
    student.birth_date = _birth_date_in(2)
    db.commit()

    _run_birthday_check()

    assert _count_for(db, chief.id) == 1


def test_ignores_birthday_far_away(db, user_factory):
    chief = user_factory(vk_id=700_003, name="ГП", role_name="админ")
    student = user_factory(vk_id=700_004, name="Пётр Сидоров")
    student.birth_date = _birth_date_in(20)
    db.commit()

    _run_birthday_check()

    assert _count_for(db, chief.id) == 0


def test_notifies_same_day(db, user_factory):
    chief = user_factory(vk_id=700_005, name="ГП", role_name="админ")
    student = user_factory(vk_id=700_006, name="Оля Кузнецова")
    student.birth_date = _birth_date_in(0)
    db.commit()

    _run_birthday_check()

    notif = db.query(Notification).filter(Notification.user_id == chief.id).first()
    assert notif is not None
    assert "сегодня" in notif.text


def test_does_not_repeat_within_same_year(db, user_factory):
    chief = user_factory(vk_id=700_007, name="ГП", role_name="админ")
    student = user_factory(vk_id=700_008, name="Игорь Смирнов")
    student.birth_date = _birth_date_in(3)
    student.birthday_reminder_sent_year = today_msk().year
    db.commit()

    _run_birthday_check()

    assert _count_for(db, chief.id) == 0


def test_skips_student_without_birth_date(db, user_factory):
    chief = user_factory(vk_id=700_010, name="ГП", role_name="админ")
    user_factory(vk_id=700_011, name="Без даты")
    db.commit()

    _run_birthday_check()

    assert _count_for(db, chief.id) == 0


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

    chief = user_factory(vk_id=700_015, name="ГП", role_name="админ")
    student = user_factory(vk_id=700_016, name="Январский Именинник")
    student.birth_date = date(2010, 1, 3)
    db.commit()

    monkeypatch.setattr(scheduler_module, "today_msk", lambda: date(2026, 12, 27))
    _run_birthday_check()

    monkeypatch.setattr(scheduler_module, "today_msk", lambda: date(2027, 1, 1))
    _run_birthday_check()

    assert _count_for(db, chief.id) == 1


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


def test_chief_teacher_sees_birthday_on_staff_screen(db, client, session_factory, user_factory):
    """ГП открывает напоминание колокольчиком → «Все уведомления →»; пункта
    меню у него нет, экран тот же, что у куратора."""
    chief = user_factory(vk_id=700_036, name="ГП", role_name="админ")
    student = user_factory(vk_id=700_037, name="Соня Белова")
    student.birth_date = _birth_date_in(6)
    db.commit()
    _run_birthday_check()
    client.cookies.set("session_id", session_factory(chief).id)

    resp = client.get("/cabinet/staff/notifications")

    assert resp.status_code == 200
    assert "День рождения — Соня Белова" in resp.text


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


def test_extra_recipient_gets_birthday_too(db, user_factory, monkeypatch):
    """«Служба заботы» (владелец 29.09.2026) — к ней привязан Telegram Лизы.
    Получает вместе с ГП, неактивный дополнительный получатель — нет."""
    import app.services.exam_scheduler as scheduler_module

    chief = user_factory(vk_id=700_040, name="ГП", role_name="админ")
    care = user_factory(vk_id=700_041, name="служба заботы")
    gone = user_factory(vk_id=700_042, name="Бывший получатель", is_active=False)
    monkeypatch.setattr(
        scheduler_module, "BIRTHDAY_EXTRA_RECIPIENT_IDS", frozenset({care.id, gone.id}),
    )
    student = user_factory(vk_id=700_043, name="Юля Зайцева")
    student.birth_date = _birth_date_in(4)
    db.commit()

    _run_birthday_check()

    assert _count_for(db, chief.id) == 1
    assert _count_for(db, care.id) == 1
    assert _count_for(db, gone.id) == 0
