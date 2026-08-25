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


# ── /cabinet/personal/contacts — правка только контактов ─────────────────────

def test_contacts_form_shows_editable_and_locked_fields(auth_client, db):
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
    # контакты — поля ввода
    assert 'name="phone"' in resp.text
    assert 'name="parent_phone"' in resp.text
    assert 'name="tg_username"' in resp.text
    # установочные данные — только показ, поля для них нет
    assert 'name="first_name"' not in resp.text
    assert 'name="last_name"' not in resp.text
    assert 'name="tariff"' not in resp.text
    assert "Анна Смирнова" in resp.text


def test_contacts_redirects_to_profile_when_incomplete(client, user_factory, session_factory):
    user = user_factory(vk_id=100_301, profile_completed=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet/personal/contacts", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/profile"


def test_contacts_post_saves_phone_and_username(auth_client, db):
    from app.models.user import User
    client, user = auth_client

    resp = client.post("/cabinet/personal/contacts", data={
        "phone": "+79001112233",
        "parent_phone": "+79002223344",
        "tg_username": "@new_nick",
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/personal?saved=1"

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.phone == "+79001112233"
    assert saved.parent_phone == "+79002223344"
    assert saved.tg_username == "new_nick"  # «@» срезается на сохранении


def test_contacts_post_does_not_touch_setup_fields(auth_client, db):
    """Установочные данные не меняются, даже если их подложили в форму."""
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
        "first_name": "Взломщик",
        "last_name": "Подменённый",
        "tariff": "МАКСИМУМ",
        "university_year": "2030",
    }, follow_redirects=False)

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.name == "Анна Смирнова"
    assert saved.tariff == "УВЕРЕННЫЙ"
    assert saved.university_year is None


def test_contacts_post_invalid_phone_shows_error(auth_client, db):
    from app.models.user import User
    client, user = auth_client

    resp = client.post("/cabinet/personal/contacts", data={
        "phone": "телефон",
        "parent_phone": "+79002223344",
        "tg_username": "anna_art",
    })
    assert resp.status_code == 200
    assert "Введите корректный номер телефона" in resp.text

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.phone != "телефон"


def test_contacts_post_short_username_shows_error(auth_client):
    client, _ = auth_client
    resp = client.post("/cabinet/personal/contacts", data={
        "phone": "+79001112233",
        "parent_phone": "+79002223344",
        "tg_username": "ab",
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
    }, follow_redirects=False)
    assert resp.status_code == 302

    db.expire_all()
    saved = db.query(User).filter(User.id == user.id).first()
    assert saved.phone == "+79001112233"
