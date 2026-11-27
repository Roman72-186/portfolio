"""Поиск и фильтры списка «Проверка по ученику» (владелец 28.09.2026).

Экран снят 05.10.2026 и возвращён 06.10.2026 в меню ГП и суперадмина: куратору
адрес уводит в «Учеников», поэтому здесь только ГП. Фильтры только сужают
круг, который задаёт `_accessible_students`.
"""
from datetime import datetime, timezone

from app.models.task_block import BLOCK_UPLOAD, TaskBlock, TaskBlockSubmission
from app.models.tracker import ITEM_MOCK_EXAM, TrackerTask

URL = "/cabinet/staff/students-review"


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


def test_status_filter_splits_unchecked_and_checked(db, user_factory, session_factory, client, rendered):
    head = user_factory(vk_id=870_031, name="ГП", role_name="админ")
    busy = user_factory(vk_id=870_032, name="Есть Непроверенное")
    clear = user_factory(vk_id=870_033, name="Всё Сдано")
    task = TrackerTask(title="Пробник", kind=ITEM_MOCK_EXAM, is_published=True)
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type=BLOCK_UPLOAD)
    db.add(block)
    db.flush()
    db.add(TaskBlockSubmission(block_id=block.id, user_id=busy.id, submitted_at=datetime.now(timezone.utc)))
    db.commit()
    client = _as(client, session_factory, head)

    for status, expected in (("unchecked", busy), ("checked", clear)):
        assert client.get(URL, params={"status": status}).status_code == 200
        assert [row["student"].id for row in rendered("staff_students_review.html")["rows"]] == [expected.id]


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
