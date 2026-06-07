"""Tests for user management service guard rails."""

from app.services.user_management import soft_delete_user, toggle_user_active


def test_admin_cannot_block_superadmin(db, user_factory):
    admin = user_factory(vk_id=910001, name="Admin", role_name="админ", is_admin=True)
    superadmin = user_factory(vk_id=910002, name="Superadmin", role_name="суперадмин", is_admin=True)

    result = toggle_user_active(db, target_user_id=superadmin.id, performed_by_id=admin.id)

    db.refresh(superadmin)
    assert result is None
    assert superadmin.is_active is True


def test_admin_cannot_soft_delete_peer_admin(db, user_factory):
    admin = user_factory(vk_id=910003, name="Admin", role_name="админ", is_admin=True)
    peer = user_factory(vk_id=910004, name="Peer Admin", role_name="админ", is_admin=True)

    result = soft_delete_user(db, target_user_id=peer.id, performed_by_id=admin.id)

    db.refresh(peer)
    assert result is False
    assert peer.deleted_at is None


def test_superadmin_can_block_lower_rank_admin(db, user_factory):
    superadmin = user_factory(vk_id=910005, name="Superadmin", role_name="суперадмин", is_admin=True)
    admin = user_factory(vk_id=910006, name="Admin", role_name="админ", is_admin=True)

    result = toggle_user_active(db, target_user_id=admin.id, performed_by_id=superadmin.id)

    db.refresh(admin)
    assert result is False
    assert admin.is_active is False
