"""Точка А: балл 0–100 за весь набор работ «До», ставит только ГП.

Владелец 09.09.2026: «их может оценить ГП, это будет для высчета среднего
значения в точке А»; на вопрос «кто ставит оценку» — «только Главный
преподаватель» (роль ранга 4, в интерфейсе так и подписана).
"""
import pytest

from app.models.user import User

URL = "/cabinet/staff/students-review/portfolio-before/{}/score"


# CSRF в тестах отключён (`conftest.py` подменяет `require_csrf_header`), так
# что заголовок здесь не шлём и отдельного теста на него нет — проверять было
# бы нечего.


@pytest.fixture()
def student(user_factory):
    return user_factory(vk_id=860_001, name="Ученик Точкин")


def _as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def test_head_teacher_scores_the_whole_before_set(client, db, user_factory, session_factory, student):
    admin = user_factory(vk_id=860_002, name="Главный преподаватель", role_name="админ")
    _as(client, session_factory, admin)

    resp = client.post(URL.format(student.id), json={"score": 87})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "score": 87}
    fresh = db.query(User).filter(User.id == student.id).first()
    assert fresh.portfolio_before_score == 87
    assert fresh.portfolio_before_scored_by_id == admin.id
    assert fresh.portfolio_before_scored_at is not None


def test_superadmin_can_score_too(client, db, user_factory, session_factory, student):
    boss = user_factory(vk_id=860_003, name="Суперадмин", role_name="суперадмин")
    _as(client, session_factory, boss)

    assert client.post(URL.format(student.id), json={"score": 55}).status_code == 200


@pytest.mark.parametrize("role_name", ["куратор", "модератор"])
def test_curator_and_moderator_are_refused(
    client, db, user_factory, session_factory, student, role_name
):
    staff = user_factory(vk_id=860_010 + len(role_name), name="Сотрудник", role_name=role_name)
    student.curator_id = staff.id
    db.commit()
    _as(client, session_factory, staff)

    resp = client.post(URL.format(student.id), json={"score": 70})

    assert resp.status_code == 403
    assert db.query(User).filter(User.id == student.id).first().portfolio_before_score is None


def test_student_cannot_score_anybody(client, db, user_factory, session_factory, student):
    other = user_factory(vk_id=860_020, name="Другой ученик")
    _as(client, session_factory, other)

    assert client.post(URL.format(student.id), json={"score": 70}).status_code == 403


@pytest.mark.parametrize("score", [-1, 101, 1000])
def test_score_outside_the_scale_is_refused(
    client, db, user_factory, session_factory, student, score
):
    admin = user_factory(vk_id=860_030, name="ГП", role_name="админ")
    _as(client, session_factory, admin)

    assert client.post(URL.format(student.id), json={"score": score}).status_code == 422


def test_extra_fields_are_refused(client, db, user_factory, session_factory, student):
    """Тело `extra=forbid`: лишнее поле — опечатка вызывающего, не тихий no-op."""
    admin = user_factory(vk_id=860_031, name="ГП", role_name="админ")
    _as(client, session_factory, admin)

    resp = client.post(URL.format(student.id), json={"score": 60, "comment": "ок"})

    assert resp.status_code == 422


def test_unknown_student_is_404(client, db, user_factory, session_factory):
    admin = user_factory(vk_id=860_032, name="ГП", role_name="админ")
    _as(client, session_factory, admin)

    assert client.post(URL.format(999_999), json={"score": 60}).status_code == 404


def test_second_score_overwrites_the_first(client, db, user_factory, session_factory, student):
    admin = user_factory(vk_id=860_033, name="ГП", role_name="админ")
    _as(client, session_factory, admin)

    client.post(URL.format(student.id), json={"score": 40})
    first_at = db.query(User).filter(User.id == student.id).first().portfolio_before_scored_at
    client.post(URL.format(student.id), json={"score": 90})

    fresh = db.query(User).filter(User.id == student.id).first()
    assert fresh.portfolio_before_score == 90
    assert fresh.portfolio_before_scored_at >= first_at
