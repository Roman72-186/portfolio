"""Тесты Web Push подписки (Фаза 3).

Эндпоинты:
  POST /cabinet/push/subscribe    — сохранить/обновить подписку браузера
  POST /cabinet/push/unsubscribe  — выключить тумблер (is_active=False)
"""

from app.models.push_subscription import PushSubscription


def _payload(endpoint="https://fcm.googleapis.com/abc123", p256dh="p256dh-key", auth="auth-key"):
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def test_subscribe_creates_row(auth_client, db):
    client, user = auth_client
    r = client.post("/cabinet/push/subscribe", json=_payload())
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    sub = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).first()
    assert sub is not None
    assert sub.endpoint == "https://fcm.googleapis.com/abc123"
    assert sub.p256dh == "p256dh-key"
    assert sub.auth_key == "auth-key"
    assert sub.is_active is True


def test_subscribe_same_endpoint_reactivates_and_reassigns(auth_client, db, user_factory):
    client, user = auth_client
    other = user_factory(vk_id=222_222, name="Other Student")
    db.add(PushSubscription(
        user_id=other.id, endpoint="https://fcm.googleapis.com/shared",
        p256dh="old", auth_key="old", is_active=False,
    ))
    db.commit()

    r = client.post("/cabinet/push/subscribe", json=_payload(endpoint="https://fcm.googleapis.com/shared"))
    assert r.status_code == 200

    rows = db.query(PushSubscription).filter(PushSubscription.endpoint == "https://fcm.googleapis.com/shared").all()
    assert len(rows) == 1
    assert rows[0].user_id == user.id
    assert rows[0].is_active is True
    assert rows[0].p256dh == "p256dh-key"


def test_subscribe_missing_fields_returns_400(auth_client, db):
    client, user = auth_client
    r = client.post("/cabinet/push/subscribe", json={"endpoint": "", "keys": {}})
    assert r.status_code == 400
    assert db.query(PushSubscription).count() == 0


def test_unsubscribe_sets_inactive_without_deleting(auth_client, db):
    client, user = auth_client
    client.post("/cabinet/push/subscribe", json=_payload())

    r = client.post("/cabinet/push/unsubscribe", json={"endpoint": "https://fcm.googleapis.com/abc123"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    sub = db.query(PushSubscription).filter(PushSubscription.user_id == user.id).first()
    assert sub is not None
    assert sub.is_active is False


def test_unsubscribe_cannot_deactivate_other_users_subscription(auth_client, db, user_factory):
    client, user = auth_client
    other = user_factory(vk_id=333_333, name="Other Student")
    db.add(PushSubscription(
        user_id=other.id, endpoint="https://fcm.googleapis.com/not-mine",
        p256dh="k", auth_key="a", is_active=True,
    ))
    db.commit()

    r = client.post("/cabinet/push/unsubscribe", json={"endpoint": "https://fcm.googleapis.com/not-mine"})
    assert r.status_code == 200

    sub = db.query(PushSubscription).filter(PushSubscription.endpoint == "https://fcm.googleapis.com/not-mine").first()
    assert sub.is_active is True


def test_status_is_scoped_and_logout_deactivates_device(auth_client, db, user_factory):
    client, user = auth_client
    endpoint = "https://fcm.googleapis.com/device"
    other = user_factory(vk_id=555_123)
    db.add(PushSubscription(user_id=other.id, endpoint=endpoint, p256dh="k", auth_key="a", is_active=True))
    db.commit()
    status = client.post("/cabinet/push/status", json={"endpoint": endpoint})
    assert status.json() == {"ok": True, "active": False}
    assert client.post("/cabinet/push/subscribe", json=_payload(endpoint)).status_code == 200
    assert client.post("/logout", follow_redirects=False).status_code == 302
    db.expire_all()
    sub = db.query(PushSubscription).filter_by(endpoint=endpoint).one()
    assert sub.user_id == user.id
    assert sub.is_active is False


def test_subscription_refuses_unknown_provider(auth_client):
    client, _ = auth_client
    response = client.post("/cabinet/push/subscribe", json=_payload("https://unrelated.example.test/push"))
    assert response.status_code == 400
