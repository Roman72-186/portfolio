"""Экран «Ученики»: проверка годов в анкете ученика (аудит 29.09.2026,
план `plans/2026-09-29-apparchi-students-phone.md`).

Остальные сторожа аудита проверяли CSS и JS текстом и сняты 20.10.2026
(`plans/2026-10-20-тесты-чистка.md`).
"""

import pytest


# Шаг 4.2: ошибки годов говорили про поле, которое на экране подписано иначе.
# Неверный год — отказ с причиной, и в базе ничего не меняется.
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enrollment_year", "1990"),
        ("enrollment_year", "двадцать"),
        ("university_year", "3000"),
        ("university_year", "2026г"),
    ],
)
def test_bad_year_is_refused_and_not_saved(
    client, db, session_factory, user_factory, field, value
):
    staff = user_factory(vk_id=777_042, name="Админ", role_name="админ", is_admin=True)
    student = user_factory(vk_id=100_842)
    before = getattr(student, field)
    client.cookies.set("session_id", session_factory(staff).id)

    resp = client.post(
        f"/cabinet/students/{student.id}/profile",
        data={"first_name": "Иван", "last_name": "Петров", "phone": "+79990000000", field: value},
    )

    assert resp.status_code == 400
    assert resp.json()["errors"]
    db.refresh(student)
    assert getattr(student, field) == before
