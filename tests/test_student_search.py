"""Поиск ученика по номеру на платформе — везде, где ищут учеников (владелец
14.10.2026: «добавить везде поиск по id»).

Правило одно: `services/student_search.py` на сервере и
`static/js/student-search.js` в браузере. Запрос из одних цифр («256»,
«#256», «№ 256», «id 256») находит ученика с этим номером и только его.
"""
import pytest

from app.services.review_aggregate import filter_review_rows
from app.services.student_search import student_id_query, student_matches


@pytest.mark.parametrize("query, expected", [
    ("256", 256),
    (" #256 ", 256),
    ("№ 256", 256),
    ("id256", 256),
    ("ID 256", 256),
    ("", None),
    ("Анна", None),
    ("anna256", None),
    ("25 6", None),
    ("@256", None),
])
def test_student_id_query(query, expected):
    assert student_id_query(query) == expected


def test_student_matches_by_id_exactly_not_by_substring(db, user_factory):
    student = user_factory(vk_id=880_001, name="Иванова Анна")
    other = user_factory(vk_id=880_002, name="Петрова Мария")
    student.tg_username = "@anna_iv"
    db.commit()

    assert student_matches(student, str(student.id))
    assert student_matches(student, f"#{student.id}")
    assert not student_matches(other, str(student.id))
    # Номер — не подстрока: «1» не находит #12.
    assert not student_matches(student, f"{student.id}0")
    assert student_matches(student, "анна иванова")
    assert student_matches(student, "@anna_iv")
    assert not student_matches(student, "@anna_iv", search_contacts=False)
    assert student_matches(student, "")


def _as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def test_users_page_finds_by_id(db, client, session_factory, user_factory):
    chief = user_factory(vk_id=880_010, name="Главный", role_name="суперадмин")
    found = user_factory(vk_id=880_011, name="Искомая Ученица")
    user_factory(vk_id=880_012, name="Посторонний Ученик")
    db.commit()
    client = _as(client, session_factory, chief)

    page = client.get("/cabinet/superadmin/users", params={"q": f"#{found.id}"}).text

    assert "Искомая Ученица" in page
    assert "Посторонний Ученик" not in page


def test_tags_page_finds_by_id(db, client, session_factory, user_factory):
    head = user_factory(vk_id=880_020, name="ГП", role_name="админ")
    found = user_factory(vk_id=880_021, name="Искомая Ученица")
    user_factory(vk_id=880_022, name="Посторонний Ученик")
    db.commit()
    client = _as(client, session_factory, head)

    page = client.get("/cabinet/superadmin/tags", params={"q": str(found.id)}).text

    assert "Искомая Ученица" in page
    assert "Посторонний Ученик" not in page


def test_review_rows_filter_by_id(db, user_factory):
    found = user_factory(vk_id=880_031, name="Искомая Ученица")
    other = user_factory(vk_id=880_032, name="Посторонний Ученик")
    rows = [{"student": found, "unchecked": 0}, {"student": other, "unchecked": 0}]

    result = filter_review_rows(rows, q=f"id {found.id}")

    assert [r["student"].id for r in result] == [found.id]
