"""Поиск и фильтры списка «Проверка по ученику» (владелец 28.09.2026).

Фильтры только сужают круг, который задаёт `_accessible_students`: куратор не
расширит его, подставив в адрес чужой `curator`, а поиск по @username и VK ID
открыт только с ГП, как и контакты на странице «Ученики».
"""
from app.models.work import Work, WORK_TYPE_MOCK_EXAM

URL = "/cabinet/staff/students-review"


def _unchecked_work(db, user_id):
    db.add(Work(
        user_id=user_id, work_type=WORK_TYPE_MOCK_EXAM, month="сентябрь", year=2026,
        filename="final.jpg", s3_url="https://s3.example.com/final.jpg",
        subject="Рисунок", status="success", is_final=True, score=None,
    ))
    db.commit()


def _as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def test_search_matches_every_word_ignoring_case_and_yo(db, user_factory, session_factory, client):
    head = user_factory(vk_id=870_001, name="ГП", role_name="админ")
    alena = user_factory(vk_id=870_002, name="Алёна")
    alena.last_name, alena.first_name = "Сёмина", "Алёна"
    other = user_factory(vk_id=870_003, name="Другой Ученик")
    db.commit()

    resp = _as(client, session_factory, head).get(URL, params={"q": "  семина   АЛЕНА "})

    assert resp.status_code == 200
    assert "Алёна" in resp.text
    assert other.name not in resp.text
    assert "Найдено 1 из 2" in resp.text


def test_head_teacher_finds_student_by_username_not_vk_id(db, user_factory, session_factory, client):
    """Поиск по нику Telegram. По `vk_id` — нет: VK удалён 29.09.2026, номер
    внутренний и людям не показывается, искать по нему некому."""
    head = user_factory(vk_id=870_011, name="ГП", role_name="админ")
    found = user_factory(vk_id=870_012, name="Искомый")
    found.tg_username = "@Iskomy_Nick"
    user_factory(vk_id=870_013, name="Посторонний")
    db.commit()
    client = _as(client, session_factory, head)

    by_nick = client.get(URL, params={"q": "@iskomy_nick"}).text
    by_vk = client.get(URL, params={"q": "870012"}).text

    assert "Искомый" in by_nick
    assert "Посторонний" not in by_nick
    assert "Искомый" not in by_vk


def test_curator_search_ignores_contacts(db, user_factory, session_factory, client):
    curator = user_factory(vk_id=870_021, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=870_022, name="Свой")
    student.curator_id = curator.id
    student.tg_username = "secret_nick"
    db.commit()

    resp = _as(client, session_factory, curator).get(URL, params={"q": "secret_nick"})

    assert "Свой" not in resp.text
    assert "По этим условиям никого нет" in resp.text


def test_status_filter_splits_unchecked_and_checked(db, user_factory, session_factory, client):
    head = user_factory(vk_id=870_031, name="ГП", role_name="админ")
    busy = user_factory(vk_id=870_032, name="Есть Непроверенное")
    user_factory(vk_id=870_033, name="Всё Сдано")
    _unchecked_work(db, busy.id)
    client = _as(client, session_factory, head)

    unchecked = client.get(URL, params={"status": "unchecked"}).text
    checked = client.get(URL, params={"status": "checked"}).text

    assert "Есть Непроверенное" in unchecked and "Всё Сдано" not in unchecked
    assert "Всё Сдано" in checked and "Есть Непроверенное" not in checked


def test_tariff_filter_and_newcomer_without_tariff(db, user_factory, session_factory, client):
    head = user_factory(vk_id=870_041, name="ГП", role_name="админ")
    user_factory(vk_id=870_042, name="С Тарифом", tariff="Я САМ")
    newcomer = user_factory(vk_id=870_043, name="Новенький Пробник")
    newcomer.tariff = ""
    db.commit()
    client = _as(client, session_factory, head)

    by_tariff = client.get(URL, params={"tariff": "Я САМ"}).text
    newcomers = client.get(URL, params={"tariff": "__newcomer__"}).text

    assert "С Тарифом" in by_tariff and "Новенький Пробник" not in by_tariff
    assert "Новенький Пробник" in newcomers and "С Тарифом" not in newcomers


def test_head_teacher_filters_by_curator_and_without_curator(db, user_factory, session_factory, client):
    head = user_factory(vk_id=870_051, name="ГП", role_name="админ")
    curator = user_factory(vk_id=870_052, name="Куратор Анна", role_name="куратор")
    assigned = user_factory(vk_id=870_053, name="У Анны")
    assigned.curator_id = curator.id
    user_factory(vk_id=870_054, name="Ничей")
    db.commit()
    client = _as(client, session_factory, head)

    page = client.get(URL).text
    by_curator = client.get(URL, params={"curator": str(curator.id)}).text
    unassigned = client.get(URL, params={"curator": "none"}).text

    assert f'<option value="{curator.id}"' in page
    assert "У Анны" in by_curator and "Ничей" not in by_curator
    assert "Ничей" in unassigned and "У Анны" not in unassigned


def test_curator_cannot_widen_scope_with_curator_param(db, user_factory, session_factory, client):
    own = user_factory(vk_id=870_061, name="Куратор свой", role_name="куратор")
    other = user_factory(vk_id=870_062, name="Куратор чужой", role_name="куратор")
    mine = user_factory(vk_id=870_063, name="Мой ученик")
    mine.curator_id = own.id
    foreign = user_factory(vk_id=870_064, name="Чужой ученик")
    foreign.curator_id = other.id
    db.commit()

    resp = _as(client, session_factory, own).get(URL, params={"curator": str(other.id)})

    assert "Чужой ученик" not in resp.text
    assert "Мой ученик" in resp.text
    assert 'name="curator"' not in resp.text
