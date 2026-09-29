"""Экран загрузки голосовых уровней точки А — `/cabinet/staff/point-a-audio`."""
from unittest.mock import patch

import pytest

from app.api import cabinet_point_a_audio
from app.models.notification import Notification
from app.models.point_a_level_audio import PointALevelAudio
from app.services import media_transcode


def _fake_voice(filename, data, content_type):
    return "voice.m4a", b"aac-bytes", "audio/mp4"


@pytest.fixture(autouse=True)
def fake_transcode():
    """Перекодирование здесь подменено: настоящий ffmpeg гоняет
    `tests/test_media_transcode.py`, а этим тестам важен путь до S3."""
    with patch.object(media_transcode, "playable_voice", side_effect=_fake_voice) as voice:
        yield voice


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
    assert resp.status_code == 200
    assert "формате mp3" in resp.text


def test_over_size_limit_rejected(client, admin, session_factory):
    _as(client, session_factory, admin)
    with patch.object(cabinet_point_a_audio, "MAX_POINT_A_AUDIO_SIZE", 10):
        resp = client.post(
            "/cabinet/staff/point-a-audio/1",
            files={"audio": ("voice.mp3", b"x" * 11, "audio/mpeg")},
        )
    assert resp.status_code == 200
    assert "Голосовое больше" in resp.text


def test_limit_is_the_common_voice_limit():
    """15 МБ держались ради `sendVoice` по ссылке. С 29.09.2026 Telegram
    получает сам файл, лимит — общий для голосовых."""
    from app.services import feedback as fb_service
    assert cabinet_point_a_audio.MAX_POINT_A_AUDIO_SIZE == fb_service.MAX_FEEDBACK_AUDIO_SIZE


def test_rejection_shown_only_under_its_own_card(client, admin, session_factory):
    _as(client, session_factory, admin)
    resp = client.post(
        "/cabinet/staff/point-a-audio/2",
        files={"audio": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 200
    level_1_card, level_2_card = resp.text.split('data-level="2"')
    assert "формате mp3" not in level_1_card
    assert "формате mp3" in level_2_card


def test_unknown_level_is_404(client, admin, session_factory):
    _as(client, session_factory, admin)
    resp = client.post(
        "/cabinet/staff/point-a-audio/3",
        files={"audio": ("voice.mp3", b"x" * 10, "audio/mpeg")},
    )
    assert resp.status_code == 404


def test_upload_is_transcoded_to_m4a(client, db, admin, session_factory, fake_transcode):
    """Запись из браузера (webm) iPhone не доигрывает — до S3 она становится m4a."""
    _as(client, session_factory, admin)
    with patch(
        "app.services.point_a_level_audio.s3_service.upload_to_s3",
        return_value="https://s3.example.com/point-a-audio/1/x.m4a",
    ) as upload:
        resp = client.post(
            "/cabinet/staff/point-a-audio/1",
            files={"audio": ("voice-1.webm", b"webm-bytes", "audio/webm;codecs=opus")},
        )

    assert resp.status_code == 200, resp.text
    assert fake_transcode.call_args.args == ("voice-1.webm", b"webm-bytes", "audio/webm")
    path, data, mime = upload.call_args.args
    assert path.startswith("point-a-audio/1/") and path.endswith(".m4a")
    assert (data, mime) == (b"aac-bytes", "audio/mp4")
    saved = db.query(PointALevelAudio).filter(PointALevelAudio.level == 1).one()
    assert saved.audio_s3_path == path


@pytest.mark.parametrize("files", [
    pytest.param({"audio": ("", b"", "application/octet-stream")}, id="пустое-поле"),
    pytest.param(None, id="без-поля"),
])
def test_empty_form_is_text_not_raw_json(client, admin, session_factory, files):
    """Поле файла скрыто и без `required` — пустое «Сохранить» должно дать
    подсказку у формы, а не сырой JSON 422."""
    _as(client, session_factory, admin)
    resp = client.post(
        "/cabinet/staff/point-a-audio/1",
        files=files, data=None if files else {"x": "1"},
    )
    assert resp.status_code == 200, resp.text
    assert "Запишите голосовое или прикрепите файл" in resp.text


def test_screen_offers_recording(client, admin, session_factory):
    _as(client, session_factory, admin)
    page = client.get("/cabinet/staff/point-a-audio").text
    assert page.count('data-media-recorder data-mrf-modes="voice"') == 2
    assert "/static/js/media-recorder-field.js?v=" in page
    assert "/static/css/media-recorder.css?v=" in page
    # Скрытое поле с `required` браузер молча не даёт отправить.
    assert 'name="audio" accept="audio/*" hidden>' in page
    assert "required" not in page.split("prg-items", 1)[1]
    assert "window.csrfFresh()" in page


def test_replaced_audio_already_sent_stays_in_s3(client, db, admin, session_factory):
    """Ссылка на голосовое лежит в уведомлениях учеников — удалить старый
    файл при замене значит оставить им немой плеер."""
    _as(client, session_factory, admin)
    first_url = "https://s3.example.com/point-a-audio/1/first.m4a"
    with patch(
        "app.services.point_a_level_audio.s3_service.upload_to_s3",
        side_effect=[first_url, "https://s3.example.com/point-a-audio/1/second.m4a"],
    ), patch("app.services.point_a_level_audio.s3_service.delete_from_s3") as delete:
        client.post("/cabinet/staff/point-a-audio/1", files={"audio": ("a.mp3", b"a", "audio/mpeg")})
        db.add(Notification(user_id=admin.id, title="Точка А", audio_url=first_url))
        db.commit()
        client.post("/cabinet/staff/point-a-audio/1", files={"audio": ("b.mp3", b"b", "audio/mpeg")})

    delete.assert_not_called()
