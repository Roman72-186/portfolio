"""Tests for user management service guard rails."""

from app.models.audit_log import AuditLog
from app.services.user_management import (
    can_assign_role_rank,
    can_manage_user,
    can_manage_user_by_rank,
    soft_delete_user,
    toggle_user_active,
)


def test_rank_helpers_match_current_admin_rules(db, user_factory):
    superadmin = user_factory(vk_id=910014, name="Superadmin", role_name="суперадмин", is_admin=True)
    admin = user_factory(vk_id=910015, name="Admin", role_name="админ", is_admin=True)
    curator = user_factory(vk_id=910016, name="Curator", role_name="куратор")

    assert can_manage_user(superadmin, admin) is True
    assert can_manage_user(admin, superadmin) is False
    assert can_manage_user(admin, admin) is False
    assert can_manage_user_by_rank(admin.id, 4, curator) is True
    assert can_assign_role_rank(5, 4) is True
    assert can_assign_role_rank(4, 4) is False
    assert can_assign_role_rank(2, 1) is False


def test_admin_cannot_block_self(db, user_factory):
    admin = user_factory(vk_id=910007, name="Admin", role_name="админ", is_admin=True)

    result = toggle_user_active(db, target_user_id=admin.id, performed_by_id=admin.id)

    db.refresh(admin)
    assert result is None
    assert admin.is_active is True


def test_admin_cannot_block_superadmin(db, user_factory):
    admin = user_factory(vk_id=910001, name="Admin", role_name="админ", is_admin=True)
    superadmin = user_factory(vk_id=910002, name="Superadmin", role_name="суперадмин", is_admin=True)

    result = toggle_user_active(db, target_user_id=superadmin.id, performed_by_id=admin.id)

    db.refresh(superadmin)
    assert result is None
    assert superadmin.is_active is True


def test_curator_cannot_block_student(db, user_factory):
    curator = user_factory(vk_id=910008, name="Curator", role_name="куратор")
    student = user_factory(vk_id=910009, name="Student", role_name="ученик")
    student.curator_id = curator.id
    db.commit()

    result = toggle_user_active(db, target_user_id=student.id, performed_by_id=curator.id)

    db.refresh(student)
    assert result is None
    assert student.is_active is True


def test_admin_cannot_soft_delete_peer_admin(db, user_factory):
    admin = user_factory(vk_id=910003, name="Admin", role_name="админ", is_admin=True)
    peer = user_factory(vk_id=910004, name="Peer Admin", role_name="админ", is_admin=True)

    result = soft_delete_user(db, target_user_id=peer.id, performed_by_id=admin.id)

    db.refresh(peer)
    assert result is False
    assert peer.deleted_at is None


def test_superadmin_can_block_lower_rank_admin(db, user_factory, session_factory):
    superadmin = user_factory(vk_id=910005, name="Superadmin", role_name="суперадмин", is_admin=True)
    admin = user_factory(vk_id=910006, name="Admin", role_name="админ", is_admin=True)
    session = session_factory(admin)

    result = toggle_user_active(db, target_user_id=admin.id, performed_by_id=superadmin.id)

    db.refresh(admin)
    db.refresh(session)
    assert result is False
    assert admin.is_active is False
    assert session.is_active is False


def test_superadmin_can_soft_delete_lower_rank_admin_and_audit(db, user_factory, session_factory):
    superadmin = user_factory(vk_id=910010, name="Superadmin", role_name="суперадмин", is_admin=True)
    admin = user_factory(vk_id=910011, name="Admin", role_name="админ", is_admin=True)
    session = session_factory(admin)

    result = soft_delete_user(db, target_user_id=admin.id, performed_by_id=superadmin.id)

    db.refresh(admin)
    db.refresh(session)
    audit = db.query(AuditLog).filter(
        AuditLog.action == "user_delete",
        AuditLog.performed_by_id == superadmin.id,
        AuditLog.target_user_id == admin.id,
    ).first()
    assert result is True
    assert admin.deleted_at is not None
    assert admin.is_active is False
    assert session.is_active is False
    assert audit is not None


def test_deleted_user_cannot_be_toggled(db, user_factory):
    superadmin = user_factory(vk_id=910012, name="Superadmin", role_name="суперадмин", is_admin=True)
    student = user_factory(vk_id=910013, name="Deleted Student", role_name="ученик")
    assert soft_delete_user(db, target_user_id=student.id, performed_by_id=superadmin.id) is True

    result = toggle_user_active(db, target_user_id=student.id, performed_by_id=superadmin.id)

    db.refresh(student)
    assert result is None
    assert student.is_active is False
