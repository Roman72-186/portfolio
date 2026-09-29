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


# ── Шаг 2 плана (`/adapt`) ───────────────────────────────────────────────────


def _phone_block(source: str) -> str:
    start = source.index("@media (max-width: 834px) {")
    end = source.index("@media (max-width: 768px) {", start)
    return source[start:end]


def test_hero_wraps_so_upload_button_fits_on_phone():
    source = _source()
    mobile = source[source.index("@media (max-width: 768px) {"):source.index("</style>")]
    assert re.search(r"\.student-hero\s*\{[^}]*flex-wrap: wrap", mobile), (
        "шапка в одну строку с overflow:hidden — «+ Загрузить» обрезана на 320–390"
    )
    assert "margin-left:auto\">'" in source and "openUploadModal()\">+ Загрузить" in source


def test_back_gesture_walks_screen_history():
    source = _source()
    assert "window.addEventListener('popstate'" in source
    assert "history[push ? 'pushState' : 'replaceState']" in source
    # Выбор ученика и открытие вкладки пишут историю, «назад» проверяет несохранённый балл.
    assert "navCommit(navPush, 'list')" in source and "navCommit(navPush, 'profile')" in source
    popstate = source[source.index("window.addEventListener('popstate'"):]
    assert "guardUnsavedScore()" in popstate[:1200]
    # Замена адреса с null стёрла бы запись навигации экрана.
    assert "history.replaceState(null" not in source


def test_phone_inputs_are_16px_and_targets_44px():
    block = _phone_block(_source())
    fonts = block[:block.index("font-size: 16px")]
    for sel in (".sidebar-search", ".sidebar-select", ".score-input", ".comment-input",
                ".rt-editable", ".profile-edit-input", ".profile-edit-select", ".upload-select"):
        assert sel in fonts, f"{sel} мельче 16px — айфон приближает страницу при касании"
    targets = block[:block.index("min-height: 44px; }")]
    for sel in (".tab-btn", ".back-to-profile", ".mobile-back-btn", ".admin-upload-btn", ".btn-edit-score",
                ".profile-edit-btn", ".mock-month-btn", ".cal-month-btn", ".mock-status-copy-btn"):
        assert sel in targets, f"{sel} меньше 44px на касание"
    assert ".hard-filter-options a" in block and ".sidebar-hard-filters-add summary" in block
    assert re.search(r"\.mock-day, #main-panel \.cal-day \{[^}]*min-height: 44px", block)
