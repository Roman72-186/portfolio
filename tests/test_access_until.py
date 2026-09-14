"""Срок доступа ученика (`User.access_until`) — пробный набор предобучения.

Владелец 11.09.2026: «доступ закрыт и он остаётся только на экране Личная
информация, ссылка на поддержку». Отсюда три вещи, которые тут проверяются:
закрытое закрыто, «Личная информация» открыта, и никого лишнего срок не задел.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.navigation import student_nav_items
from app.services.tz import msk_input_value, msk_text, parse_msk_local


def _hours_ago(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _hours_ahead(hours: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# ── Гейт: что закрыто и что открыто ──────────────────────────────────────────

def test_expired_student_redirected_from_learning(db, client, session_factory, user_factory):
    """Истёк срок — учебный раздел уводит на «Личную информацию»."""
    student = user_factory()
    student.access_until = _hours_ago(1)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/learning", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/personal"


def test_expired_student_can_open_personal(db, client, session_factory, user_factory):
    """«Личная информация» остаётся открытой — иначе человеку некуда прийти
    за оплатой."""
    student = user_factory()
    student.access_until = _hours_ago(1)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/personal")

    assert resp.status_code == 200
    assert "Доступ закрыт" in resp.text
    # Меню проверяем на самой странице, а не вызовом `student_nav_items`:
    # признак едет через шаблон с `|default(false)`, и потеряйся он по дороге —
    # ученик получил бы полное меню из ссылок, которые тут же его отбрасывают.
    assert "/cabinet/learning" not in resp.text
    assert "/cabinet/tracker" not in resp.text


def test_expired_student_api_gets_403_json(db, client, session_factory, user_factory):
    """Фоновому запросу отдаём честный 403, а не редирект на HTML-страницу —
    иначе экран молча покажет пустоту вместо причины."""
    student = user_factory()
    student.access_until = _hours_ago(1)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/tracker", headers={"accept": "application/json"})

    assert resp.status_code == 403


def test_future_deadline_does_not_block(db, client, session_factory, user_factory):
    """Срок ещё не наступил — доступ обычный."""
    student = user_factory()
    student.access_until = _hours_ahead(24)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    assert client.get("/cabinet/personal").status_code == 200
    assert "Доступ закрыт" not in client.get("/cabinet/personal").text


def test_no_deadline_does_not_block(auth_client):
    """Пустой срок — как у всех действующих учеников, ничего не меняется."""
    client, _student = auth_client

    assert client.get("/cabinet/personal").status_code == 200


def test_expired_deadline_ignored_for_staff(db, client, session_factory, user_factory):
    """Случайная дата на строке сотрудника не должна запирать ему кабинет:
    срок держит только учеников."""
    staff = user_factory(
        vk_id=555_001, name="Куратор", role_name="куратор", is_admin=False,
    )
    staff.access_until = _hours_ago(1)
    db.commit()
    client.cookies.set("session_id", session_factory(staff).id)

    resp = client.get("/cabinet/students", follow_redirects=False)

    assert resp.status_code != 302 or resp.headers.get("location") != "/cabinet/personal"


def test_expired_student_without_profile_does_not_loop(db, client, session_factory, user_factory):
    """Незаполненная анкета плюс истёкший срок — самая опасная пара: редирект
    на анкету упирался бы в запрет и отбрасывал обратно, и страница зациклилась
    бы насмерть."""
    student = user_factory(profile_completed=False)
    student.access_until = _hours_ago(1)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/personal", follow_redirects=False)

    assert resp.status_code == 200


# ── Навигация ────────────────────────────────────────────────────────────────

def test_nav_keeps_only_personal_when_expired():
    items = student_nav_items(access_expired=True)

    assert [item.key for item in items] == ["personal"]


def test_nav_full_by_default():
    assert len(student_nav_items()) > 1
    assert len(student_nav_items(access_expired=False)) > 1


# ── Время ────────────────────────────────────────────────────────────────────

def test_msk_helpers_round_trip():
    """Что показали в форме, то и приняли назад: `datetime-local` ходит в
    московском времени, в базе лежит UTC."""
    parsed = parse_msk_local("2026-09-27T23:30")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert msk_input_value(parsed) == "2026-09-27T23:30"
    assert msk_text(parsed) == "27.09.2026 в 23:30"


def test_msk_helpers_treat_naive_as_utc():
    """SQLite отдаёт время без таймзоны — считаем его UTC, а не временем
    машины, иначе отсечка съедет на часовой пояс разработчика."""
    naive = datetime(2026, 9, 27, 20, 30)

    assert msk_text(naive) == "27.09.2026 в 23:30"


def test_msk_helpers_on_empty():
    assert msk_input_value(None) == ""
    assert msk_text(None) == ""


# ── Правка срока персоналом ──────────────────────────────────────────────────

@pytest.fixture()
def staff_client(client, session_factory, user_factory):
    staff = user_factory(vk_id=777_001, name="Админ", role_name="админ", is_admin=True)
    client.cookies.set("session_id", session_factory(staff).id)
    return client, staff


def _profile_form(**extra):
    body = {
        "first_name": "Иван",
        "last_name": "Петров",
        "phone": "+79990000000",
    }
    body.update(extra)
    return body


def test_staff_sets_access_until(db, staff_client, user_factory):
    client, _staff = staff_client
    student = user_factory(vk_id=100_777)

    resp = client.post(
        f"/cabinet/students/{student.id}/profile",
        data=_profile_form(access_until="2026-09-27T23:30"),
    )

    assert resp.status_code == 200, resp.text
    db.refresh(student)
    assert msk_input_value(student.access_until) == "2026-09-27T23:30"


def test_staff_clears_access_until(db, staff_client, user_factory):
    """Пустое поле снимает ограничение — так оплативший возвращается к учёбе."""
    client, _staff = staff_client
    student = user_factory(vk_id=100_778)
    student.access_until = _hours_ago(1)
    db.commit()

    resp = client.post(
        f"/cabinet/students/{student.id}/profile", data=_profile_form(access_until=""),
    )

    assert resp.status_code == 200, resp.text
    db.refresh(student)
    assert student.access_until is None


def test_staff_gets_error_on_broken_date(db, staff_client, user_factory):
    """Мусор в поле — ошибка, а не молчаливое снятие срока: иначе опечатка
    открывала бы доступ тому, кому его закрывают."""
    client, _staff = staff_client
    student = user_factory(vk_id=100_779)
    student.access_until = _hours_ago(1)
    db.commit()
    before = student.access_until

    resp = client.post(
        f"/cabinet/students/{student.id}/profile", data=_profile_form(access_until="не дата"),
    )

    assert resp.status_code == 400
    db.refresh(student)
    assert student.access_until == before


# ── Анкета новичка: без выбора тарифа ────────────────────────────────────────
#
# Владелец 14.09.2026: пришедший по ссылке пробного набора знакомится с
# платформой без тарифа, а выбирает его потом, когда срок заканчивается.
# Признак новичка — тот же `access_until`, других меток источника входа в базе
# нет.

def _anketa_data(**extra):
    """Валидная анкета без поля `tariff` — его добавляют тесты, которым нужно."""
    data = {
        "first_name": "Анна",
        "last_name": "Смирнова",
        "birth_date": "2010-05-20",
        "city": "Москва",
        "timezone": "0",
        "phone": "+79001112233",
        "parent_phone": "+79002223344",
        "parent_name": "Ольга Викторовна",
        "vk_profile_url": "vk.com/anna_smirnova",
        "sdek_address": "Москва, ул. Ленина 10, ПВЗ Строгино",
        "email": "anna@example.com",
        "tg_username": "anna_art",
        "university_year": "2027",
    }
    data.update(extra)
    return data


def test_intake_student_profile_form_has_no_tariff_step(
    db, client, session_factory, user_factory
):
    """Новичку со сроком доступа шаг «Тариф обучения» не показываем, и анкета
    становится трёхшаговой."""
    student = user_factory(vk_id=100_301, profile_completed=False, tariff="")
    student.access_until = _hours_ahead(48)
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/profile")

    assert resp.status_code == 200
    assert 'id="tariff-select"' not in resp.text
    assert "Тариф обучения" not in resp.text
    assert resp.text.count('class="prf-section-title"') == 3
    assert "Шаг 3 из 3 · Учёба" in resp.text


def test_regular_student_profile_form_keeps_tariff_step(
    db, client, session_factory, user_factory
):
    """Действующему ученику без срока доступа анкета остаётся прежней — иначе
    правило задело бы тех, ради кого выбор тарифа и сделан."""
    student = user_factory(vk_id=100_302, profile_completed=False, tariff="")
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/profile")

    assert resp.status_code == 200
    assert 'id="tariff-select"' in resp.text
    assert "Шаг 3 из 4 · Тариф обучения" in resp.text


def test_intake_student_saves_profile_without_tariff(
    db, client, session_factory, user_factory
):
    """Анкета новичка сохраняется без тарифа: без этого форма падала бы с 422
    на отсутствующем поле."""
    from app.models.user import User

    student = user_factory(vk_id=100_303, profile_completed=False, tariff="")
    student.access_until = _hours_ahead(48)
    db.commit()
    student_id = student.id
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.post("/cabinet/profile", data=_anketa_data(), follow_redirects=False)

    assert resp.status_code == 302
    db.expire_all()
    saved = db.query(User).filter(User.id == student_id).first()
    assert saved.profile_completed is True
    assert not saved.tariff


def test_intake_student_submitted_tariff_ignored(
    db, client, session_factory, user_factory
):
    """Подделанный POST с тарифом ничего не записывает: шага в форме нет,
    значит и значения быть не может."""
    from app.models.user import User

    student = user_factory(vk_id=100_304, profile_completed=False, tariff="")
    student.access_until = _hours_ahead(48)
    db.commit()
    student_id = student.id
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.post(
        "/cabinet/profile",
        data=_anketa_data(tariff="Уверенный", past_tariffs=["Я сам"]),
        follow_redirects=False,
    )

    assert resp.status_code == 302
    db.expire_all()
    saved = db.query(User).filter(User.id == student_id).first()
    assert not saved.tariff
    assert saved.past_tariffs is None


def test_intake_student_keeps_tariff_set_by_curator(
    db, client, session_factory, user_factory
):
    """Если куратор успел проставить тариф заранее, анкета новичка его не
    затирает пустотой."""
    from app.models.user import User

    student = user_factory(vk_id=100_305, profile_completed=False, tariff="УВЕРЕННЫЙ")
    student.access_until = _hours_ahead(48)
    db.commit()
    student_id = student.id
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.post("/cabinet/profile", data=_anketa_data(), follow_redirects=False)

    assert resp.status_code == 302
    db.expire_all()
    saved = db.query(User).filter(User.id == student_id).first()
    assert saved.tariff == "УВЕРЕННЫЙ"


def test_regular_student_still_must_choose_tariff(
    db, client, session_factory, user_factory
):
    """У действующего ученика тариф остаётся обязательным."""
    student = user_factory(vk_id=100_306, profile_completed=False, tariff="")
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.post("/cabinet/profile", data=_anketa_data())

    assert resp.status_code == 200
    assert "Выбери тариф" in resp.text


def test_staff_deadline_clears_tariff_for_newcomer(db, staff_client, user_factory):
    """Куратор пометил сроком новичка с незаполненной анкетой — тариф снимаем.

    Этот человек вошёл напрямую с apparchi.ru, минуя ссылку `/proba`, и тариф
    у него стоит из дефолта при создании аккаунта. Анкета шаг тарифа ему уже не
    покажет, так что без этой очистки он молча остался бы на «УВЕРЕННЫЙ».
    """
    client, _staff = staff_client
    student = user_factory(vk_id=100_781, profile_completed=False, tariff="УВЕРЕННЫЙ")

    resp = client.post(
        f"/cabinet/students/{student.id}/profile",
        data=_profile_form(access_until="2026-09-27T23:30"),
    )

    assert resp.status_code == 200, resp.text
    db.refresh(student)
    assert not student.tariff


def test_staff_deadline_keeps_tariff_of_filled_profile(db, staff_client, user_factory):
    """У ученика с заполненной анкетой тариф выбран им самим — срок его не
    трогает, какой бы ни была причина ограничения."""
    client, _staff = staff_client
    student = user_factory(vk_id=100_782, profile_completed=True, tariff="УВЕРЕННЫЙ")

    resp = client.post(
        f"/cabinet/students/{student.id}/profile",
        data=_profile_form(access_until="2026-09-27T23:30"),
    )

    assert resp.status_code == 200, resp.text
    db.refresh(student)
    assert student.tariff == "УВЕРЕННЫЙ"


def test_staff_explicit_tariff_wins_over_clearing(db, staff_client, user_factory):
    """Куратор в той же форме выбрал тариф — значение куратора главнее."""
    client, _staff = staff_client
    student = user_factory(vk_id=100_783, profile_completed=False, tariff="")

    resp = client.post(
        f"/cabinet/students/{student.id}/profile",
        data=_profile_form(access_until="2026-09-27T23:30", tariff="УВЕРЕННЫЙ"),
    )

    assert resp.status_code == 200, resp.text
    db.refresh(student)
    assert student.tariff == "УВЕРЕННЫЙ"
