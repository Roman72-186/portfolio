"""Экран «Ученики» с телефона (аудит 29.09.2026, план `plans/2026-09-29-apparchi-students-phone.md`).

Сторожа тех правок аудита, которые видно без браузера. Замеры, ради которых
они сделаны, сняты живым прогоном на 320–768 px:

- окно «Загрузить работы» с 12 фото пробника было выше экрана и не
  прокручивалось — на 375×667 кнопка «Загрузить» уходила за нижний край,
  заголовок и крестик за верхний;
- крестик удаления фото появлялся только при наведении мыши: на телефоне его
  не видно, а невидимая кнопка 44px ловила касание в угол фото;
- в «Пробниках» и «Отработках» крестик жил вне `.photo-wrap` и рисовался голой
  кнопкой 12×19, а удалённое фото пробника оставалось на экране;
- сохранения экрана брали CSRF-ключ из разметки, а вкладку с учениками
  держат открытой весь день (тот же класс отказа, что 26.09.2026 у учеников).
"""

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "templates" / "cabinet_students.html"


def _source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _css_rule(source: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", source)
    assert match, f"нет правила {selector}"
    return match.group(1)


def test_upload_modal_scrolls_when_taller_than_screen():
    source = _source()
    overlay = _css_rule(source, ".upload-modal-overlay")
    assert "overflow-y: auto" in overlay, "окно выше экрана нельзя прокрутить до «Загрузить»"
    # Центрирование через align-items:center прячет верх окна за экран, когда
    # оно не влезает; auto-поля центрируют только то, что влезло.
    assert "align-items: center" not in overlay
    assert "margin: auto 0" in _css_rule(source, ".upload-modal")


def test_delete_cross_is_visible_without_hover():
    source = _source()
    rule = _css_rule(source, ".photo-del")
    assert "opacity: 0" not in rule, "крестик спрятан по умолчанию — на телефоне его не видно"
    assert "width: 44px" in rule and "height: 44px" in rule
    # Прятать можно только там, где есть наведение мышью.
    assert re.search(r"@media \(hover: hover\)\s*\{[^}]*\.photo-del\s*\{\s*opacity: 0", source)
    assert ".photo-wrap .photo-del {" not in source, (
        "стиль крестика только внутри .photo-wrap — в пробниках и отработках он голый"
    )


def test_mock_photo_delete_uses_shared_wrapper_and_rerenders():
    source = _source()
    assert "'<div style=\"position:relative;display:block\">' + img + badge + delBtn" not in source
    assert "'<div class=\"photo-wrap\" style=\"display:block\">' + img + badge + delBtn" in source
    assert "_currentTab === 'mock-exams' && el.closest('.mock-day-card')" in source, (
        "удалённое фото пробника остаётся на экране вместе с формой оценки"
    )


def test_student_screen_mutations_send_fresh_token():
    source = _source()
    assert "append('csrf_token', CSRF_TOKEN)" not in source, "ключ из разметки вшит в поле запроса"
    for url_part in (
        "'/profile', { method: 'POST'",
        "'/retake', {",
        "'/revision', {",
        "'/subject', {",
        "'/works/bulk', {",
        "'/portfolio/month', {",
        "'/move', {",
    ):
        line = next((ln for ln in source.splitlines() if url_part in ln), "")
        assert "window.csrfFetch(" in line, f"мутация {url_part} уходит без свежего ключа"
    # Обычные формы (балл, разблокировка пересдачи) подменяют ключ перед отправкой.
    assert source.count('onsubmit="return submitWithFreshToken(this)"') == 2
    assert "this.form.submit()" not in source, "отправка в обход свежего ключа и проверки поля"
