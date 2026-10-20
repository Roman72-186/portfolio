"""Tests for login generation: _transliterate, _make_login."""

import pytest

from app.api.cabinet_superadmin import _transliterate, _make_login


# ---------------------------------------------------------------------------
# _transliterate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Иван", "ivan"),
        ("Мария", "maria"),
        ("Роман", "roman"),
        ("ИВАН", "ivan"),
        ("Ivan", "ivan"),
        ("", ""),
    ],
)
def test_transliterate(name, expected):
    assert _transliterate(name) == expected


# ---------------------------------------------------------------------------
# _make_login
# ---------------------------------------------------------------------------

def test_make_login_simple(db, user_factory):
    user = user_factory(vk_id=700001, name="Ivan Petrov", role_name="куратор")
    user.first_name = "Иван"
    user.last_name = "Петров"
    db.add(user)
    db.commit()

    result = _make_login(user, db)
    assert result == "ivan.p"


def test_make_login_no_last_name(db, user_factory):
    user = user_factory(vk_id=700002, name="Иван", role_name="куратор")
    user.first_name = "Иван"
    user.last_name = None
    db.add(user)
    db.commit()

    result = _make_login(user, db)
    assert result == "ivan"


def test_make_login_empty_name_fallback(db, user_factory):
    user = user_factory(vk_id=700004, name="X", role_name="куратор")
    user.first_name = None
    user.last_name = None
    user.name = ""
    db.add(user)
    db.commit()

    result = _make_login(user, db)
    assert result == "user"


def test_make_login_collision_adds_suffix(db, user_factory):
    # Create user that already has login ivan.p
    existing = user_factory(vk_id=700010, name="Existing", role_name="куратор")
    existing.staff_login = "ivan.p"
    db.add(existing)
    db.commit()

    # New user with same name initials
    new_user = user_factory(vk_id=700011, name="Ivan P", role_name="куратор")
    new_user.first_name = "Иван"
    new_user.last_name = "Петров"
    db.add(new_user)
    db.commit()

    result = _make_login(new_user, db)
    assert result == "ivan.p2"


def test_make_login_multiple_collisions(db, user_factory):
    for i, login in enumerate(["ivan.p", "ivan.p2"]):
        u = user_factory(vk_id=700020 + i, name=f"User{i}", role_name="куратор")
        u.staff_login = login
        db.add(u)
    db.commit()

    new_user = user_factory(vk_id=700030, name="Ivan P3", role_name="куратор")
    new_user.first_name = "Иван"
    new_user.last_name = "Петров"
    db.add(new_user)
    db.commit()

    result = _make_login(new_user, db)
    assert result == "ivan.p3"


def test_make_login_max_length(db, user_factory):
    user = user_factory(vk_id=700040, name="X", role_name="куратор")
    user.first_name = "Александра"
    user.last_name = "Константинопольская"
    db.add(user)
    db.commit()

    result = _make_login(user, db)
    assert len(result) <= 20


def test_make_login_only_safe_chars(db, user_factory):
    import string
    user = user_factory(vk_id=700050, name="Test", role_name="куратор")
    user.first_name = "Иван"
    user.last_name = "Петров"
    db.add(user)
    db.commit()

    result = _make_login(user, db)
    allowed = set(string.ascii_lowercase + string.digits + ".")
    assert all(c in allowed for c in result)
