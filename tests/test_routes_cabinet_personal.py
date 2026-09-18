"""Tests for GET /cabinet/personal — «Личная информация» (трек A)."""


def test_personal_without_auth_redirects(client):
    resp = client.get("/cabinet/personal", follow_redirects=False)
    assert resp.status_code == 302
    assert "session_expired" in resp.headers["location"]


def test_personal_redirects_to_profile_when_incomplete(client, user_factory, session_factory):
    user = user_factory(profile_completed=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet/personal", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/profile"


def test_personal_shows_own_contacts(auth_client, db):
    client, user = auth_client
    user.phone = "+79991234567"
    user.tg_username = "self_view"
    db.add(user)
    db.commit()

    resp = client.get("/cabinet/personal")
    assert resp.status_code == 200
    assert "+79991234567" in resp.text
    assert "@self_view" in resp.text


def test_personal_shows_anketa_data(auth_client, db):
    """Личная информация показывает поля анкеты первого входа (12-13.09.2026:
    дата рождения, город, часовой пояс, email, ВКонтакте, родитель, СДЭК),
    а не только контакты и тариф."""
    from datetime import date

    client, user = auth_client
    user.birth_date = date(2008, 5, 20)
    user.city = "Казань"
    user.timezone = "3"
    user.email = "anna@example.com"
    user.vk_profile_url = "https://vk.com/anna_smirnova"
    user.parent_name = "Смирнова Мария Петровна"
    user.sdek_address = "Казань, ул. Ленина, 1"
    db.add(user)
    db.commit()

    resp = client.get("/cabinet/personal")
    assert resp.status_code == 200
    assert "20.05.2008" in resp.text
    assert "Казань" in resp.text
    assert "МСК+3" in resp.text
    assert "anna@example.com" in resp.text
    assert "https://vk.com/anna_smirnova" in resp.text
    assert "Смирнова Мария Петровна" in resp.text
    assert "Казань, ул. Ленина, 1" in resp.text


def test_personal_shows_placeholder_when_contact_missing(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/personal")
    assert resp.status_code == 200
    assert "Не указано" in resp.text


def test_personal_bottom_nav_highlights_personal_tab(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/personal")
    assert resp.status_code == 200
    assert 'href="/cabinet/personal"' in resp.text
    assert 'class="bottom-nav"' in resp.text


def test_personal_keeps_platform_guide_available(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/personal")

    assert resp.status_code == 200
    assert "Как пользоваться платформой" in resp.text
    assert 'onclick="openStudentOnboardingGuide()"' in resp.text
    assert 'id="studentOnboardingGuide"' in resp.text


# ── /cabinet/personal/contacts — правка личных данных ────────────────────────

def test_contacts_form_shows_personal_fields_and_locks_access_fields(auth_client, db):
    client, user = auth_client
    user.first_name = "Анна"
    user.last_name = "Смирнова"
    user.name = "Анна Смирнова"
    user.phone = "+79991234567"
    user.tg_username = "anna_art"
    db.add(user)
    db.commit()

    resp = client.get("/cabinet/personal/contacts")
    assert resp.status_code == 200
    # Все личные данные ученика доступны для правки.
    assert 'name="first_name"' in resp.text
    assert 'name="last_name"' in resp.text
    assert 'name="birth_date"' in resp.text
    assert 'name="phone"' in resp.text
    assert 'name="parent_phone"' in resp.text
    assert 'name="parent_name"' in resp.text
    assert 'name="tg_username"' in resp.text
    assert 'name="city"' in resp.text
    assert 'name="timezone"' in resp.text
    assert 'name="email"' in resp.text
    assert 'name="vk_profile_url"' in resp.text
    assert 'name="sdek_address"' in resp.text
    assert 'name="university_year"' in resp.text
    # Поля, управляющие доступом и учебным прогрессом, доступны только staff.
    assert 'name="tariff"' not in resp.text
    assert 'name="enrollment_year"' not in resp.text
    assert 'value="Анна"' in resp.text
    assert 'value="Смирнова"' in resp.text


def test_contacts_redirects_to_profile_when_incomplete(client, user_factory, session_factory):
    user = user_factory(vk_id=100_301, profile_completed=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet/personal/contacts", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/profile"


_VALID_CONTACTS_EXTRA = {
    "first_name": "Анна",
    "last_name": "Смирнова",
    "birth_date": "2008-05-20",
    "parent_name": "Мария Петровна",
    "university_year": "2027",
    "city": "Казань",
    "timezone": "3",
    "email": "anna@example.com",
    "vk_profile_url": "vk.com/anna_smirnova",
    "sdek_address": "Казань, ул. Ленина, 1",
}


def test_contacts_post_saves_phone_and_username(auth_client, db):
    from app.models.user import User
    client, user = auth_client

    resp = client.post("/cabinet/personal/contacts", data={
        "phone": "+79001112233",
        "parent_phone": "+79002223344",
        "tg_username": "@new_nick",
        **_VALID_CONTACTS_EXTRA,
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/personal?saved=1"

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.phone == "+79001112233"
    assert saved.parent_phone == "+79002223344"
    assert saved.tg_username == "new_nick"  # «@» срезается на сохранении
    assert saved.city == "Казань"
    assert saved.timezone == "3"
    assert saved.email == "anna@example.com"
    assert saved.vk_profile_url == "https://vk.com/anna_smirnova"
    assert saved.sdek_address == "Казань, ул. Ленина, 1"


def test_contacts_post_saves_personal_fields_but_not_access_fields(auth_client, db):
    """Ученик меняет личные поля, но не тариф и даты учебного доступа."""
    from app.models.user import User
    client, user = auth_client
    user.first_name = "Анна"
    user.last_name = "Смирнова"
    user.name = "Анна Смирнова"
    db.add(user)
    db.commit()

    client.post("/cabinet/personal/contacts", data={
        "phone": "+79001112233",
        "parent_phone": "+79002223344",
        "tg_username": "anna_art",
        **_VALID_CONTACTS_EXTRA,
        "first_name": "Мария",
        "last_name": "Петрова",
        "birth_date": "2007-04-19",
        "parent_name": "Ольга Сергеевна",
        "university_year": "2028",
        "tariff": "МАКСИМУМ",
        "enrollment_year": "2030",
    }, follow_redirects=False)

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.name == "Мария Петрова"
    assert saved.birth_date.isoformat() == "2007-04-19"
    assert saved.parent_name == "Ольга Сергеевна"
    assert saved.university_year == 2028
    assert saved.tariff == "УВЕРЕННЫЙ"
    assert saved.enrollment_year is None


def test_contacts_post_invalid_phone_shows_error(auth_client, db):
    from app.models.user import User
    client, user = auth_client

    resp = client.post("/cabinet/personal/contacts", data={
        "phone": "телефон",
        "parent_phone": "+79002223344",
        "tg_username": "anna_art",
        **_VALID_CONTACTS_EXTRA,
    })
    assert resp.status_code == 200
    assert "Номер нужен российский" in resp.text

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.phone != "телефон"


def test_contacts_post_normalizuet_nomer_s_vosmerkoy(auth_client, db):
    """Привычный ввод «8 (900) …» ложится в базу каноном `+7XXXXXXXXXX`."""
    from app.models.user import User
    client, user = auth_client

    resp = client.post("/cabinet/personal/contacts", data={
        "phone": "8 (900) 111-22-33",
        "parent_phone": "+7 900 222-33-44",
        "tg_username": "anna_art",
        **_VALID_CONTACTS_EXTRA,
    }, follow_redirects=False)
    assert resp.status_code == 302

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.phone == "+79001112233"
    assert saved.parent_phone == "+79002223344"


def test_contacts_post_short_username_shows_error(auth_client):
    client, _ = auth_client
    resp = client.post("/cabinet/personal/contacts", data={
        "phone": "+79001112233",
        "parent_phone": "+79002223344",
        "tg_username": "ab",
        **_VALID_CONTACTS_EXTRA,
    })
    assert resp.status_code == 200
    assert "Ник Telegram" in resp.text


def test_personal_links_to_contacts_screen(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/personal")
    assert resp.status_code == 200
    assert 'href="/cabinet/personal/contacts"' in resp.text
    assert 'href="/cabinet/profile"' not in resp.text


def test_contacts_post_rejects_username_taken_by_another_student(
    auth_client, db, user_factory
):
    """Ник — ключ поиска папки в Drive и заведения учеников: дубль не пускаем."""
    from app.models.user import User
    client, user = auth_client
    other = user_factory(vk_id=100_302, name="Другой ученик")
    other.tg_username = "taken_nick"
    db.add(other)
    db.commit()

    resp = client.post("/cabinet/personal/contacts", data={
        "phone": "+79001112233",
        "parent_phone": "+79002223344",
        "tg_username": "@Taken_Nick",
        **_VALID_CONTACTS_EXTRA,
    })
    assert resp.status_code == 200
    assert "уже занят" in resp.text

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.tg_username != "Taken_Nick"


def test_contacts_post_keeps_own_username(auth_client, db):
    """Свой же ник не считается занятым — иначе не сохранить телефон."""
    from app.models.user import User
    client, user = auth_client
    user.tg_username = "my_nick"
    db.add(user)
    db.commit()

    resp = client.post("/cabinet/personal/contacts", data={
        "phone": "+79001112233",
        "parent_phone": "+79002223344",
        "tg_username": "my_nick",
        **_VALID_CONTACTS_EXTRA,
    }, follow_redirects=False)
    assert resp.status_code == 302

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.phone == "+79001112233"
