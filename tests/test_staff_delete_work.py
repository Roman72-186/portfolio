"""Удаление работ ученика сотрудником (код-ревью 28.09.2026, P1).

`feedbacks.work_id` — внешний ключ без `ondelete`. Раньше
`delete_works_with_dependents` стирал файл в S3 сразу, коммит падал на ключе,
транзакция откатывалась — строка работы оставалась, файла уже не было, в
диалоге висела битая картинка, сотрудник видел 500. Теперь работа с
обратной связью не удаляется вовсе, а файл в хранилище не трогается.

SQLite в тестах внешние ключи не исполняет, поэтому тест проверяет не
падение, а сам отказ и то, что S3 не вызывался.
"""

from unittest.mock import patch

from app.models.feedback import Feedback
from app.models.work import Work


def _work(db, student, **extra) -> Work:
    fields = dict(
        user_id=student.id, work_type="after", month="сентябрь", year=2026,
        filename="w.jpg", s3_path=f"works/{student.id}/w.jpg",
    )
    fields.update(extra)
    work = Work(**fields)
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


def _as_admin(client, session_factory, admin_user):
    client.cookies.set("session_id", session_factory(admin_user).id)


def test_staff_cannot_delete_work_with_feedback(
    client, db, session_factory, admin_user, regular_user
):
    work = _work(db, regular_user)
    db.add(Feedback(work_id=work.id, curator_id=admin_user.id))
    db.commit()
    _as_admin(client, session_factory, admin_user)

    with patch("app.services.works.s3_service.delete_from_s3") as s3_delete:
        resp = client.delete(f"/cabinet/students/{regular_user.id}/works/{work.id}")

    assert resp.status_code == 409, resp.text
    assert resp.json()["ok"] is False
    assert "обратная связь" in resp.json()["error"]
    assert resp.json()["detail"] == resp.json()["error"]
    s3_delete.assert_not_called()
    db.expire_all()
    assert db.get(Work, work.id) is not None


def test_staff_bulk_delete_refuses_folder_with_reviewed_work(
    client, db, session_factory, admin_user, regular_user
):
    plain = _work(db, regular_user, filename="a.jpg", s3_path="works/a.jpg")
    reviewed = _work(db, regular_user, filename="b.jpg", s3_path="works/b.jpg")
    db.add(Feedback(work_id=reviewed.id, curator_id=admin_user.id))
    db.commit()
    _as_admin(client, session_factory, admin_user)

    with patch("app.services.works.s3_service.delete_from_s3") as s3_delete:
        resp = client.request(
            "DELETE",
            f"/cabinet/students/{regular_user.id}/works/bulk",
            json={"work_type": "after", "month": "сентябрь", "year": 2026},
        )

    assert resp.status_code == 409, resp.text
    s3_delete.assert_not_called()
    db.expire_all()
    assert db.get(Work, plain.id) is not None
    assert db.get(Work, reviewed.id) is not None


def test_staff_deletes_work_without_feedback(
    client, db, session_factory, admin_user, regular_user
):
    work = _work(db, regular_user)
    _as_admin(client, session_factory, admin_user)

    with patch("app.services.works.s3_service.delete_from_s3") as s3_delete:
        resp = client.delete(f"/cabinet/students/{regular_user.id}/works/{work.id}")

    assert resp.status_code == 200, resp.text
    # Вместе с фото уходит его превью (шаг 5 плана students-phone, 29.09.2026).
    assert [c.args[0] for c in s3_delete.call_args_list] == [work.s3_path, f"thumbs/{work.s3_path}"]
    db.expire_all()
    assert db.get(Work, work.id) is None
