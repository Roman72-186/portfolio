"""Фото в диалоге ОС открывается на весь экран и приближается (владелец 07.10.2026:
«когда отправляется рисунок с правками, его нужно раскрывать на весь экран по
нажатию, чтобы можно было рассмотреть, сделать zoom»).

До этого в пробнике фото открывалось без приближения, а в диалогах заданий и
домашки не открывалось вовсе: курсор-лупа был, действия не было. Просмотрщик
один на все экраны — `partials/lightbox.html` + `static/js/lightbox.js`.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"
LIGHTBOX_JS = ROOT / "app" / "static" / "js" / "lightbox.js"
LIGHTBOX_HTML = TEMPLATES / "partials" / "lightbox.html"

# Все три диалога ОС: пробник, работа в задании, домашка.
FEEDBACK_DIALOGS = (
    "cabinet_feedback_detail.html",
    "task_block_feedback_detail.html",
    "homework_feedback_detail.html",
)


def test_feedback_dialog_photo_opens_viewer():
    for name in FEEDBACK_DIALOGS:
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert '{% include "partials/lightbox.html" %}' in source, name
        assert 'class="photo-zoom-button' in source, name
        assert "openGallery(this.firstElementChild)" in source, name


def test_viewer_zooms():
    js = LIGHTBOX_JS.read_text(encoding="utf-8")
    html = LIGHTBOX_HTML.read_text(encoding="utf-8")
    # Кнопки «−/+» видны всем ролям — вне блока поворота.
    rotate_block = html.index("{% if lightbox_can_rotate %}\n            <div")
    assert html.index('id="lightbox-zoom-in"') < rotate_block
    assert 'id="lightbox-frame"' in html
    assert "window.lightboxZoomBy" in js
    # Щипок и перетаскивание — на Pointer Events; свайп-листание живёт там же.
    assert "pointerdown" in js and "pointermove" in js
    assert "'wheel'" in js
