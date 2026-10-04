"""Поворот фото в просмотрщике — у всех ролей, каждой в своей зоне (владелец
04.10.2026): ученик крутит свои работы, куратор — работы своих учеников,
Главный преподаватель и суперадмин — любое фото, модератор только смотрит.
Правило — `app/services/photo_rotation.py::can_rotate`.
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.models.feedback import Feedback, FeedbackMessage
from app.models.work import WORK_TYPE_AFTER, Work
from app.services import s3 as s3_service
from tests.test_work_thumbs import _FakeS3, _jpeg, _login, _with

BUCKET = "https://s3.example.com/"
LIGHTBOX = Path(__file__).resolve().parent.parent / "app" / "templates" / "partials" / "lightbox.html"


def _bucket_urls():
    return patch.object(
        s3_service, "s3_path_from_public_url",
        side_effect=lambda u: u[len(BUCKET):] if u.startswith(BUCKET) else None,
    )


def _work(db, owner, path):
    work = Work(
        user_id=owner.id, work_type=WORK_TYPE_AFTER, month="октябрь", year=2026,
        filename=path.rsplit("/", 1)[-1], s3_path=path, s3_url=BUCKET + path, status="success",
    )
    db.add(work)
    db.commit()
    return work


def _rotate(client, path, fake):
    fake.objects.setdefault(path, _jpeg(800, 600))
    with _with(fake), _bucket_urls():
        return client.post("/cabinet/rotate-photo", data={"src": BUCKET + path, "direction": "right"})


def _allowed(client, paths):
    with _bucket_urls():
        resp = client.post("/cabinet/rotate-photo/allowed", json={"srcs": [BUCKET + p for p in paths]})
    assert resp.status_code == 200, resp.text
    return {u[len(BUCKET):] for u in resp.json()["allowed"]}


def _people(db, user_factory):
    curator = user_factory(vk_id=970_001, name="Куратор", role_name="куратор")
    other_curator = user_factory(vk_id=970_002, name="Чужой куратор", role_name="куратор")
    student = user_factory(vk_id=970_003, name="Ученик")
    other_student = user_factory(vk_id=970_004, name="Чужой ученик")
    student.curator_id = curator.id
    other_student.curator_id = other_curator.id
    db.commit()
    return curator, student, other_student


# ── Ученик ───────────────────────────────────────────────────────────────────

def test_student_rotates_own_work(client, db, user_factory, session_factory):
    _, student, _ = _people(db, user_factory)
    _work(db, student, "p/own.jpg")
    _login(client, session_factory, student)

    fake = _FakeS3()
    resp = _rotate(client, "p/own.jpg", fake)

    assert resp.status_code == 200, resp.text
    assert resp.json()["src"].startswith(BUCKET + "p/own.jpg?v=")


def test_student_cannot_rotate_someone_elses_work(client, db, user_factory, session_factory):
    _, student, other = _people(db, user_factory)
    _work(db, other, "p/other.jpg")
    _login(client, session_factory, student)

    fake = _FakeS3()
    original = _jpeg(800, 600)
    fake.objects["p/other.jpg"] = original
    resp = _rotate(client, "p/other.jpg", fake)

    assert resp.status_code == 403
    assert fake.objects["p/other.jpg"] == original


def test_student_cannot_rotate_school_content(client, db, user_factory, session_factory):
    # Билет пробника, картинка задания — файла нет ни в одной таблице работ.
    _, student, _ = _people(db, user_factory)
    _login(client, session_factory, student)

    assert _rotate(client, "tickets/t.jpg", _FakeS3()).status_code == 403


def test_student_cannot_rotate_curator_photo_in_own_dialog(client, db, user_factory, session_factory):
    curator, student, _ = _people(db, user_factory)
    work = _work(db, student, "p/final.jpg")
    fb = Feedback(work_id=work.id, curator_id=curator.id)
    db.add(fb)
    db.commit()
    db.add_all([
        FeedbackMessage(feedback_id=fb.id, sender_id=curator.id, sender_role="curator",
                        photo_s3_path="fb/curator.jpg", photo_s3_url=BUCKET + "fb/curator.jpg"),
        FeedbackMessage(feedback_id=fb.id, sender_id=student.id, sender_role="student",
                        photo_s3_path="fb/mine.jpg", photo_s3_url=BUCKET + "fb/mine.jpg"),
    ])
    db.commit()
    _login(client, session_factory, student)

    assert _allowed(client, ["fb/curator.jpg", "fb/mine.jpg", "p/final.jpg"]) == {"fb/mine.jpg", "p/final.jpg"}


# ── Куратор ──────────────────────────────────────────────────────────────────

def test_curator_rotates_only_own_students(client, db, user_factory, session_factory):
    curator, student, other = _people(db, user_factory)
    _work(db, student, "p/mine.jpg")
    _work(db, other, "p/theirs.jpg")
    _login(client, session_factory, curator)

    assert _allowed(client, ["p/mine.jpg", "p/theirs.jpg", "tickets/t.jpg"]) == {"p/mine.jpg"}
    assert _rotate(client, "p/mine.jpg", _FakeS3()).status_code == 200
    assert _rotate(client, "p/theirs.jpg", _FakeS3()).status_code == 403


def test_curator_cannot_rotate_archived_student(client, db, user_factory, session_factory):
    curator, student, _ = _people(db, user_factory)
    _work(db, student, "p/archived.jpg")
    student.archived_at = datetime.now(timezone.utc)
    student.is_active = False
    db.commit()
    _login(client, session_factory, curator)

    assert _rotate(client, "p/archived.jpg", _FakeS3()).status_code == 403


# ── ГП, суперадмин, модератор ───────────────────────────────────────────────

def test_chief_teacher_rotates_school_content(client, db, user_factory, session_factory):
    chief = user_factory(vk_id=970_010, name="Главный", is_admin=True, role_name="админ")
    _login(client, session_factory, chief)

    assert _rotate(client, "tickets/t.jpg", _FakeS3()).status_code == 200


def test_moderator_only_watches(client, db, user_factory, session_factory):
    _, student, _ = _people(db, user_factory)
    _work(db, student, "p/any.jpg")
    moderator = user_factory(vk_id=970_011, name="Модератор", role_name="модератор")
    _login(client, session_factory, moderator)

    # Общий гейт модератора закрывает оба адреса — запись ему не положена.
    with _bucket_urls():
        allowed = client.post("/cabinet/rotate-photo/allowed", json={"srcs": [BUCKET + "p/any.jpg"]})
    assert allowed.status_code == 403
    assert _rotate(client, "p/any.jpg", _FakeS3()).status_code == 403


def test_moderator_rule_holds_in_service_too(db, user_factory):
    # Ранг модератора — 4, как у ГП: без явной строки в can_rotate проверка
    # кнопок отдала бы ему всё, если общий гейт когда-нибудь пустит адрес.
    from app.services.photo_rotation import can_rotate
    assert not can_rotate(db, {"role_name": "модератор", "role_rank": 4, "user_id": 1}, "p/any.jpg")
    assert can_rotate(db, {"role_name": "админ", "role_rank": 4, "user_id": 1}, "p/any.jpg")


def test_foreign_url_is_rejected(client, db, user_factory, session_factory):
    chief = user_factory(vk_id=970_012, name="Главный", is_admin=True, role_name="админ")
    _login(client, session_factory, chief)
    fake = _FakeS3()
    with _with(fake), _bucket_urls():
        resp = client.post("/cabinet/rotate-photo", data={"src": "https://evil.example/x.jpg", "direction": "left"})
    assert resp.status_code == 400


# ── Просмотрщик ──────────────────────────────────────────────────────────────

def test_lightbox_offers_rotation_to_everyone_but_moderator():
    source = LIGHTBOX.read_text(encoding="utf-8")
    assert 'user.role_name != "модератор"' in source
    assert "role_rank|int) >= 5" not in source
    # Кнопки скрыты, пока сервер не ответил, какие фото можно крутить.
    assert 'id="lightbox-tools" hidden' in source
