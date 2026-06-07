"""Тесты для видео-отчётов куратора и статистики динамики баллов ученика."""
from unittest.mock import patch

import pytest

from app.models.curator_report import CuratorReport
from app.models.notification import Notification
from app.models.work import Work, WORK_TYPE_MOCK_EXAM


@pytest.fixture()
def curator_client(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=810001, name="Куратор Тест", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    return client, curator


@pytest.fixture()
def student(db, user_factory, curator_client):
    _, curator = curator_client
    s = user_factory(vk_id=810002, name="Ученик Один", role_name="ученик")
    s.curator_id = curator.id
    s.first_name = "Анна"
    s.last_name = "Иванова"
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _add_mock(db, user_id, *, month, year, subject, score):
    w = Work(
        user_id=user_id, work_type=WORK_TYPE_MOCK_EXAM,
        month=month, year=year, filename="t.jpg",
        s3_url="https://s3.example.com/t.jpg", status="success",
        subject=subject, score=score,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


# ── Reports ───────────────────────────────────────────────────────────────────

def test_reports_page_loads(curator_client):
    client, _ = curator_client
    resp = client.get("/cabinet/curator/reports")
    assert resp.status_code == 200
    assert "Видео-отчёт" in resp.text


def test_report_submit_creates_record_and_notifies_staff(
    curator_client, db, user_factory
):
    client, curator = curator_client
    admin = user_factory(vk_id=810050, name="Админ", role_name="админ")
    sa = user_factory(vk_id=810051, name="Супер", role_name="суперадмин")

    with patch(
        "app.api.cabinet_curator.s3_service.upload_to_s3",
        return_value="https://s3.example.com/reports/abc123.mp4",
    ):
        resp = client.post(
            "/cabinet/curator/reports",
            data={"text": "Отчёт за неделю"},
            files={"video": ("report.mp4", b"video-bytes", "video/mp4")},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/curator/reports?ok=1"

    reports = db.query(CuratorReport).filter(CuratorReport.curator_id == curator.id).all()
    assert len(reports) == 1
    assert reports[0].video_url == "https://s3.example.com/reports/abc123.mp4"

    # По одному уведомлению на админа и SA, без привязки к работе
    for staff in (admin, sa):
        notes = db.query(Notification).filter(Notification.user_id == staff.id).all()
        assert len(notes) == 1
        assert notes[0].work_id is None
        assert "https://s3.example.com/reports/abc123.mp4" in (notes[0].text or "")


def test_report_submit_rejects_non_video_file(curator_client, db):
    client, curator = curator_client
    resp = client.post(
        "/cabinet/curator/reports",
        data={"text": ""},
        files={"video": ("note.txt", b"text", "text/plain")},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "err=" in resp.headers["location"]
    assert db.query(CuratorReport).count() == 0


def test_admin_sees_all_curator_reports(admin_client, db, user_factory):
    a_client, _ = admin_client
    curator = user_factory(vk_id=810080, name="Куратор Икс", role_name="куратор")
    report = CuratorReport(
        curator_id=curator.id, video_url="https://drive.google.com/xyz", text="Привет",
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    resp = a_client.get("/cabinet/curator/reports")
    assert resp.status_code == 200
    assert "https://drive.google.com/xyz" in resp.text
    assert f'/cabinet/curator/reports/{report.id}/delete' in resp.text
    # У админа нет формы отправки
    assert 'name="video"' not in resp.text


def test_admin_can_delete_curator_report(admin_client, db, user_factory):
    a_client, _ = admin_client
    curator = user_factory(vk_id=810081, name="Куратор Удаление", role_name="куратор")
    report = CuratorReport(
        curator_id=curator.id,
        video_url="https://s3.example.com/bucket/curator-reports/1/2026-06/video.mp4",
        text="Удалить",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    with patch(
        "app.api.cabinet_curator.s3_service.s3_path_from_public_url",
        return_value="curator-reports/1/2026-06/video.mp4",
    ), patch("app.api.cabinet_curator.s3_service.delete_from_s3") as delete_mock:
        resp = a_client.post(
            f"/cabinet/curator/reports/{report.id}/delete",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/curator/reports?deleted=1"
    assert db.query(CuratorReport).filter(CuratorReport.id == report.id).first() is None
    delete_mock.assert_called_once_with("curator-reports/1/2026-06/video.mp4")


def test_curator_cannot_delete_report(curator_client, db):
    client, curator = curator_client
    report = CuratorReport(
        curator_id=curator.id,
        video_url="https://s3.example.com/reports/curator.mp4",
        text="Нельзя удалить",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    resp = client.post(
        f"/cabinet/curator/reports/{report.id}/delete",
        follow_redirects=False,
    )

    assert resp.status_code == 403
    assert db.query(CuratorReport).filter(CuratorReport.id == report.id).first() is not None


def test_reports_denied_for_student(client, db, user_factory, session_factory):
    user = user_factory(vk_id=810060, name="Студент", role_name="ученик")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    resp = client.get("/cabinet/curator/reports", follow_redirects=False)
    assert resp.status_code == 403


# ── Statistics ────────────────────────────────────────────────────────────────

def test_statistics_returns_12_points(curator_client, db, student):
    client, _ = curator_client
    _add_mock(db, student.id, month="январь", year=2026, subject="Рисунок", score=70)
    resp = client.get(f"/cabinet/students/{student.id}/statistics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["student"]["id"] == student.id
    assert len(data["points"]) == 12
    assert all("drawing" in p and "composition" in p for p in data["points"])


def test_statistics_denied_other_curator(
    client, db, user_factory, session_factory
):
    other = user_factory(vk_id=810070, name="Чужой Куратор", role_name="куратор")
    sess = session_factory(other)
    client.cookies.set("session_id", sess.id)
    # ученик другого куратора
    owner = user_factory(vk_id=810071, name="Владелец", role_name="куратор")
    s = user_factory(vk_id=810072, name="Ученик Чужой", role_name="ученик")
    s.curator_id = owner.id
    db.add(s)
    db.commit()
    db.refresh(s)
    resp = client.get(f"/cabinet/students/{s.id}/statistics", follow_redirects=False)
    assert resp.status_code == 403
