"""Экран уведомлений сотрудника помечает прочитанными только показанные.

Код-ревью 28.09.2026, P3: экран выводит 100 строк, а отмечал прочитанными все
непрочитанные разом — уведомления за пределами экрана пропадали из счётчика,
так и не показавшись. Непрочитанные идут первыми, поэтому хвост доедет при
следующем открытии.
"""
from datetime import datetime, timedelta, timezone

from app.models.notification import Notification


def test_only_shown_notifications_are_marked_read(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=970_001, name="Куратор", role_name="куратор")
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db.add_all([
        Notification(user_id=curator.id, title=f"Сдача {i}", created_at=base + timedelta(minutes=i))
        for i in range(101)
    ])
    db.commit()
    client.cookies.set("session_id", session_factory(curator).id)

    resp = client.get("/cabinet/staff/notifications")

    assert resp.status_code == 200
    db.expire_all()
    unread = db.query(Notification).filter(
        Notification.user_id == curator.id, Notification.is_read.is_(False)
    ).all()
    assert [n.title for n in unread] == ["Сдача 0"]
