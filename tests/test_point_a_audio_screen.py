"""Экран загрузки голосовых уровней точки А — `/cabinet/staff/point-a-audio`."""
from unittest.mock import patch

import pytest

from app.api import cabinet_point_a_audio
from app.models.point_a_level_audio import PointALevelAudio


@pytest.fixture()
def admin(user_factory):
    return user_factory(vk_id=862_001, name="ГП", role_name="админ")


@pytest.fixture()
def curator(user_factory):
    return user_factory(vk_id=862_002, name="Куратор", role_name="куратор")


def _as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def test_curator_cannot_reach_screen(client, curator, session_factory):
    _as(client, session_factory, curator)
    assert client.get("/cabinet/staff/point-a-audio").status_code == 403


def test_admin_sees_both_levels_empty(client, admin, session_factory):
    _as(client, session_factory, admin)
    resp = client.get("/cabinet/staff/point-a-audio")
    assert resp.status_code == 200
    assert "Уровень 1" in resp.text
    assert "Уровень 2" in resp.text


def test_upload_saves_new_level_audio(client, db, admin, session_factory):
    _as(client, session_factory, admin)
    source = b"\x00\x01\x02" * 1000

    with patch.object(
        cabinet_point_a_audio, "upsert_level_audio",
        wraps=cabinet_point_a_audio.upsert_level_audio,
    ):
        with patch(
            "app.services.point_a_level_audio.s3_service.upload_to_s3",
            return_value="https://s3.example.com/point-a-audio/1/x.mp3",
        ) as upload:
            resp = client.post(
                "/cabinet/staff/point-a-audio/1",
                files={"audio": ("voice.mp3", source, "audio/mpeg")},
            )

    assert resp.status_code in (200, 302)
    upload.assert_called_once()
    saved = db.query(PointALevelAudio).filter(PointALevelAudio.level == 1).first()
    assert saved is not None
    assert saved.audio_s3_url == "https://s3.example.com/point-a-audio/1/x.mp3"
    assert saved.uploaded_by_id == admin.id


def test_reupload_replaces_same_row(client, db, admin, session_factory):
    _as(client, session_factory, admin)

    with patch(
        "app.services.point_a_level_audio.s3_service.upload_to_s3",
        side_effect=[
            "https://s3.example.com/point-a-audio/2/first.mp3",
            "https://s3.example.com/point-a-audio/2/second.mp3",
        ],
    ), patch("app.services.point_a_level_audio.s3_service.delete_from_s3") as delete:
        client.post(
            "/cabinet/staff/point-a-audio/2",
            files={"audio": ("first.mp3", b"a" * 100, "audio/mpeg")},
        )
        client.post(
            "/cabinet/staff/point-a-audio/2",
            files={"audio": ("second.mp3", b"b" * 100, "audio/mpeg")},
        )

    rows = db.query(PointALevelAudio).filter(PointALevelAudio.level == 2).all()
    assert len(rows) == 1
    assert rows[0].audio_s3_url == "https://s3.example.com/point-a-audio/2/second.mp3"
    delete.assert_called_once()


def test_bad_format_rejected(client, admin, session_factory):
    _as(client, session_factory, admin)
    resp = client.post(
        "/cabinet/staff/point-a-audio/1",
        files={"audio": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 422


def test_over_size_limit_rejected(client, admin, session_factory):
    _as(client, session_factory, admin)
    with patch.object(cabinet_point_a_audio, "MAX_POINT_A_AUDIO_SIZE", 10):
        resp = client.post(
            "/cabinet/staff/point-a-audio/1",
            files={"audio": ("voice.mp3", b"x" * 11, "audio/mpeg")},
        )
    assert resp.status_code == 413


def test_unknown_level_is_404(client, admin, session_factory):
    _as(client, session_factory, admin)
    resp = client.post(
        "/cabinet/staff/point-a-audio/3",
        files={"audio": ("voice.mp3", b"x" * 10, "audio/mpeg")},
    )
    assert resp.status_code == 404
