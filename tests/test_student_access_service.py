import pytest
from fastapi import HTTPException

from app.services.student_access import get_student_for_staff_access


def _user_dict(user, rank: int) -> dict:
    return {"user_id": user.id, "role_rank": rank}


def test_curator_can_access_own_active_student(db, user_factory):
    curator = user_factory(vk_id=920001, name="Curator", role_name="куратор")
    student = user_factory(vk_id=920002, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.add(student)
    db.commit()

    result = get_student_for_staff_access(
        db,
        _user_dict(curator, 2),
        student.id,
        active_only=True,
        not_found_detail="Ученик не найден",
        forbidden_detail="Нет доступа к этому ученику",
    )

    assert result.id == student.id


def test_curator_cannot_access_other_curator_student(db, user_factory):
    owner = user_factory(vk_id=920003, name="Owner", role_name="куратор")
    other = user_factory(vk_id=920004, name="Other", role_name="куратор")
    student = user_factory(vk_id=920005, name="Student", role_name="ученик")
    student.curator_id = owner.id
    db.add(student)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        get_student_for_staff_access(
            db,
            _user_dict(other, 2),
            student.id,
            active_only=True,
            not_found_detail="Ученик не найден",
            forbidden_detail="Нет доступа к этому ученику",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Нет доступа к этому ученику"


def test_admin_can_access_any_active_student(db, user_factory):
    owner = user_factory(vk_id=920006, name="Owner", role_name="куратор")
    admin = user_factory(vk_id=920007, name="Admin", role_name="админ")
    student = user_factory(vk_id=920008, name="Student", role_name="ученик")
    student.curator_id = owner.id
    db.add(student)
    db.commit()

    result = get_student_for_staff_access(
        db,
        _user_dict(admin, 4),
        student.id,
        active_only=True,
        not_found_detail="Ученик не найден",
        forbidden_detail="Нет доступа к этому ученику",
    )

    assert result.id == student.id


def test_inactive_student_is_not_found_when_active_only(db, user_factory):
    admin = user_factory(vk_id=920009, name="Admin", role_name="админ")
    student = user_factory(
        vk_id=920010,
        name="Inactive Student",
        role_name="ученик",
        is_active=False,
    )

    with pytest.raises(HTTPException) as exc:
        get_student_for_staff_access(
            db,
            _user_dict(admin, 4),
            student.id,
            active_only=True,
            not_found_detail="Ученик не найден",
            forbidden_detail="Нет доступа к этому ученику",
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Ученик не найден"


def test_inactive_student_is_returned_when_active_only_false(db, user_factory):
    admin = user_factory(vk_id=920011, name="Admin", role_name="админ")
    student = user_factory(
        vk_id=920012,
        name="Inactive Student",
        role_name="ученик",
        is_active=False,
    )

    result = get_student_for_staff_access(
        db,
        _user_dict(admin, 4),
        student.id,
        not_found_detail="Ученик не найден",
        forbidden_detail="Нет доступа к этому ученику",
    )

    assert result.id == student.id
