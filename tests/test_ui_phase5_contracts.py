"""Контракты интерфейса фазы 5: страница урока и ватермарка зрителя.

Часть проверок неизбежно смотрит в исходники: JS-раннера в проекте нет, а логика
3D-лаборатории живёт только в браузере. Но всё, что отдаёт сервер, проверяется по
наблюдаемому выводу — так тест ловит поломку поведения, а не переформатирование.
"""
from pathlib import Path

from app.config import settings
from app.models.learning_video import LearningVideo


ROOT = Path(__file__).resolve().parents[1]
VIDEO_ID = "35ed80ae-8103-4528-a700-3f69ec56957d"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _configure_playback(monkeypatch):
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_api_key", "secret-api-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)


def _published_video(db) -> LearningVideo:
    video = LearningVideo(
        bunny_library_id=720058,
        bunny_video_id=VIDEO_ID,
        title="Урок фазы 5",
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.commit()
    return video


# ── Ватермарка зрителя: наблюдаемое поведение страницы ────────────────────────

def test_player_page_carries_viewer_identity(auth_client, db, monkeypatch):
    """Ватермарка — защита от пересылки записи, а не украшение.

    Если данные зрителя перестанут попадать в разметку, видео поедет дальше
    обезличенным, и по утечке нельзя будет понять, из чьего кабинета она вышла.
    """
    client, user = auth_client
    _configure_playback(monkeypatch)
    video = _published_video(db)

    page = client.get(f"/cabinet/videos/{video.id}")

    assert page.status_code == 200
    assert user.name in page.text
    assert 'class="video-watermark"' in page.text


def test_player_page_never_leaks_bunny_keys(auth_client, db, monkeypatch):
    """В браузер уходит подписанный embed-URL, ключи остаются на сервере."""
    client, _ = auth_client
    _configure_playback(monkeypatch)
    video = _published_video(db)

    page = client.get(f"/cabinet/videos/{video.id}")

    assert "secret-api-key" not in page.text
    assert "playback-key" not in page.text
    assert "iframe.mediadelivery.net" in page.text


def test_player_iframe_cannot_take_video_out_of_the_page(auth_client, db, monkeypatch):
    """Ни фуллскрина, ни PiP у iframe: оба режима выносят кадр из-под ватермарки.

    Свой фуллскрин уводит в полноэкранный контейнер вместе со слоем данных
    зрителя, поэтому разрешения провайдеру не выдаются.
    """
    client, _ = auth_client
    _configure_playback(monkeypatch)
    video = _published_video(db)

    page = client.get(f"/cabinet/videos/{video.id}")

    assert "picture-in-picture" not in page.text
    assert "allowfullscreen" not in page.text.lower()


# ── 3D-лаборатория: проверки по исходникам, JS-раннера в проекте нет ──────────

def test_3dlab_watermark_is_bounded_and_only_built_for_visible_viewer():
    """Ватермарка строится только для открытого вьюера и перекрывает кадр с запасом.

    Сетка плиток считается от размера контейнера с запасом в две плитки: без
    запаса поворот на 45° оголял углы, а без привязки к видимости слой строился
    для скрытой галереи и зря держал память.
    """
    source = _read("app/static/3dlab/js/app.js")

    assert 'viewer.classList.contains("visible")' in source
    assert "wm.replaceChildren()" in source
    assert "const overscanTiles = 2" in source
    assert "const viewerObserver = new MutationObserver(rebuild)" in source


def test_3dlab_skips_webgl_render_outside_active_3d_view():
    source = _read("app/static/3dlab/js/threeViewer.js")

    assert "function shouldRenderFrame(canvas)" in source
    assert "if (document.hidden) return false" in source
    assert '!viewer.classList.contains("visible")' in source
    assert "if (!shouldRenderFrame(canvas)) return" in source


def test_3dlab_uses_adaptive_mobile_render_quality():
    source = _read("app/static/3dlab/js/threeViewer.js")

    assert "function getTargetPixelRatio()" in source
    assert "const maxPixelRatio = isCompactViewport ? 1.5 : 2" in source
    assert "renderer.setPixelRatio(getTargetPixelRatio())" in source
    assert "isCompactViewport ? Math.min(rtSamples, 2) : rtSamples" in source
