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

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "templates" / "cabinet_students.html"
# Стили экрана с 29.09.2026 живут в файле, а не в <style> шаблона (шаг 8 плана).
STYLES = TEMPLATE.parent.parent / "static" / "css" / "cabinet_students.css"


def _source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _styles() -> str:
    return STYLES.read_text(encoding="utf-8")


def _css_rule(source: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", source)
    assert match, f"нет правила {selector}"
    return match.group(1)


def test_upload_modal_scrolls_when_taller_than_screen():
    source = _styles()
    overlay = _css_rule(source, ".upload-modal-overlay")
    assert "overflow-y: auto" in overlay, "окно выше экрана нельзя прокрутить до «Загрузить»"
    # Центрирование через align-items:center прячет верх окна за экран, когда
    # оно не влезает; auto-поля центрируют только то, что влезло.
    assert "align-items: center" not in overlay
    assert "margin: auto 0" in _css_rule(source, ".upload-modal")


def test_delete_cross_is_visible_without_hover():
    source = _styles()
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
        "'/revision', {",
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
    mobile = _mobile_block(_styles())
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
    block = _phone_block(_styles())
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


# ── Шаг 3 `/layout` ──────────────────────────────────────────────────────────


def _mobile_block(source: str) -> str:
    return source[source.index("@media (max-width: 768px) {"):]


def test_filter_header_is_compact_on_phone():
    # На 320×568 до списка было 371 px фильтров и два ученика целиком: пилюли
    # тарифов шли в две-три строки, селекты — друг под другом.
    mobile = _mobile_block(_styles())
    assert re.search(r"\.tariff-pills \{[^}]*flex-wrap: nowrap;[^}]*overflow-x: auto", mobile), (
        "тарифы переносятся в несколько строк — шапка съедает экран"
    )
    assert re.search(r"\.tariff-pill \{[^}]*flex-shrink: 0", mobile)
    assert re.search(r"\.sidebar-filters \{[^}]*flex-direction: row", mobile)
    # Компактнее — не за счёт касания.
    assert "min-height: 44px" in _css_rule(_styles(), ".tariff-pill")


def test_back_to_list_returns_to_same_place():
    source = _source()
    # Браузер запоминает запись списка, когда список уже спрятан, и возвращал наверх.
    assert "history.scrollRestoration = 'manual'" in source
    show_list = source[source.index("function navShowList()"):]
    show_list = show_list[:show_list.index("\n}\n")]
    assert "window.scrollTo(0, _listScrollY)" in show_list
    assert "row.scrollIntoView({block: 'center'})" in show_list, "открыли по ?student= — места нет, нужна строка"
    assert "if (navPush) _listScrollY = window.scrollY;" in source


def test_phone_calendar_keeps_work_above_calendar():
    # На 320 до работы и формы оценки было 530 px календаря.
    mobile = _mobile_block(_styles())
    assert re.search(r"\.mock-calendar-side, #main-panel \.cal-side \{[^}]*order: 2", mobile)
    assert re.search(r"\.mock-month-list, #main-panel \.cal-month-list \{[^}]*repeat\(6", mobile)
    source = _source()
    # День выбирают под работой — после выбора экран подводится к ней, в обоих календарях.
    handler = source[source.index("document.getElementById('main-panel').addEventListener('click'"):]
    handler = handler[:handler.index("}, true);")]
    assert "'.mock-day.has-works, .cal-day.has-works'" in handler
    assert "'.mock-day-card, .cal-detail'" in handler
    assert "matchMedia('(max-width: 768px)')" in handler


def test_upload_dropzone_is_block():
    # Зона — <label>: строчная рамка вокруг блоков рвалась, слева торчал обрывок пунктира.
    assert "display: block" in _css_rule(_styles(), ".upload-dropzone")


def test_labels_say_what_the_field_holds():
    # Шаг 4 плана (/clarify). Поле university_year — год поступления в вуз (так оно
    # названо в анкете ученика), а экран подписывал его «Курс университета» и
    # выводил «2027 курс». Флаг is_group_member при входе через Telegram значит
    # участие в канале школы, а бейдж писал «В группе VK» — владелец 29.09.2026
    # велел снять его совсем: без канала на платформу не войти.
    source = _source()
    assert "Курс университета" not in source
    assert "' курс'" not in source
    assert "группе VK" not in source
    assert "VK ID…" not in source
    assert "' циклов" not in source, "«1 циклов» — счётчик циклов идёт через pluralLabel"


def test_phone_sees_text_hidden_in_tooltips():
    # На телефоне title не показывается: почему плашка — в самом тексте.
    # Чипы «Ученик: / Куратор:» жили только в отработках, снятых 29.09.2026.
    source = _source()
    assert "Не заполнил профиль" not in source


def test_errors_tell_what_to_do_and_empty_search_says_so():
    source = _source()
    assert "'Ошибка сети'" not in source
    assert source.count("alert(NET_ERROR)") == 6
    assert "Ошибка загрузки" not in source
    # Поиск или тариф, не нашедшие никого, раньше оставляли список пустым без слов.
    assert 'id="student-list-empty" hidden' in source
    assert "emptyNote.hidden = anyShown" in source


# Шаг 4.2: ошибки годов говорили «Нереальный год ВУЗ» и «Год ВУЗ должен быть
# числом» — про поле, которое на экране подписано иначе, и без подсказки, что
# ввести. Теперь ошибка названа подписью поля и говорит, как её исправить.
@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("enrollment_year", "1990", "Начало обучения – год от 2000 до 2100"),
        ("enrollment_year", "двадцать", "Начало обучения – год цифрами, например 2025"),
        ("university_year", "3000", "Год поступления в вуз – от 2000 до 2100"),
        ("university_year", "2026г", "Год поступления в вуз – цифрами, например 2026"),
    ],
)
def test_year_errors_name_the_field_and_the_fix(
    client, session_factory, user_factory, field, value, error
):
    staff = user_factory(vk_id=777_042, name="Админ", role_name="админ", is_admin=True)
    student = user_factory(vk_id=100_842)
    client.cookies.set("session_id", session_factory(staff).id)

    resp = client.post(
        f"/cabinet/students/{student.id}/profile",
        data={"first_name": "Иван", "last_name": "Петров", "phone": "+79990000000", field: value},
    )

    assert resp.status_code == 400
    assert resp.json()["errors"] == [error]


# ── Шаг 6 `/polish` ──────────────────────────────────────────────────────────

BASE_CSS = TEMPLATE.parent.parent / "static" / "css" / "base.css"


def test_green_ink_is_readable_in_dark_theme():
    # 29.09.2026: зелёные плашки («✓ Рисунок», «Анкета заполнена», «Повторная сдача
    # открыта») в тёмной теме — контраст 2.8: `--ok` не был переопределён, как
    # `--success` и `--error`, и тёмный #047857 оставался на тёмном холсте.
    css = BASE_CSS.read_text(encoding="utf-8")
    dark = css[css.index(':root[data-theme="dark"] {'):]
    dark = dark[:dark.index("\n}")]
    assert re.search(r"--ok:\s*var\(--success\)", dark)
    assert not re.search(r"--ok-strong\s*:", dark), "--ok-strong — заливка под белым текстом, светлеть ей нельзя"
    # Единственная заливка `--ok` под белым текстом на экране — тост «Сохранено».
    assert "background: var(--ok-strong); color: #fff;" in _styles()
    assert "background: var(--ok); color: #fff" not in _styles()


def test_red_badge_passes_contrast_on_light():
    # Чистый --error (#DC2626) на розовой плашке — 4.4 при норме 4.5.
    assert "color-mix(in srgb, var(--error)" in _css_rule(_styles(), ".profile-badge.no")


def test_portfolio_month_header_fits_phone():
    source = _styles()
    # Кнопка раскрытия месяца была 32px на телефоне и 17px на iPad.
    targets = _phone_block(source)
    assert ".portfolio-toggle" in targets[:targets.index("min-height: 44px; }")]
    # На 390 «Сентябрь 2026» и «1 фото» рвались на две строки, сжатые кнопками справа.
    assert "flex-wrap: wrap" in _css_rule(source, ".month-header")
    assert "white-space: nowrap" in _css_rule(source, ".month-label")
    assert "white-space: nowrap" in _css_rule(source, ".month-count")


def test_profile_sections_are_two_by_two_on_phone():
    # Столбиком четыре раздела занимали 540 px в самом низу профиля.
    assert re.search(r"\.profile-actions \{ grid-template-columns: repeat\(2, 1fr\); \}", _mobile_block(_styles()))
    # Плашки «Учёбы сейчас» не тянутся по высоте кнопки «Проверить».
    assert "align-items: center" in _css_rule(_styles(), ".profile-status-badges")


# ── Шаг 7: разделы над анкетой ───────────────────────────────────────────────


def test_profile_sections_come_before_the_form():
    # Владелец 29.09.2026: «Портфолио», «Задания», «Пробники», «Статистика» — сразу под
    # шапкой, везде. Под 13 полями анкеты на телефоне до них было ~1100 px прокрутки.
    source = _source()
    render = source[source.index("function renderProfile(data) {"):source.index("function buildProfileActions(s) {")]
    assert "buildHero(s, s.avg_score_by_subject || null) + buildProfileActions(s)" in render
    assert render.index("buildProfileActions(s)") < render.index('<div class="profile-details">')
    assert "'<div class=\"profile-actions\">'" not in render, "кнопки разделов собираются в двух местах"
    # На компьютере — одним рядом, а не 3 + 1.
    assert "repeat(auto-fit, minmax(150px, 1fr))" in _css_rule(_styles(), ".profile-actions")


# ── Шаг 8: стили экрана — в файле ────────────────────────────────────────────


def test_screen_styles_live_in_cached_file():
    # Владелец 29.09.2026: вынести 842 строки встроенного CSS в файл — его кэширует
    # браузер, а не качает заново с каждой страницей экрана.
    source = _source()
    assert "<style" not in source, "стили экрана снова пишут в шаблон — место им в cabinet_students.css"
    assert re.search(r'<link rel="stylesheet" href="/static/css/cabinet_students\.css\?v=\d+">', source), (
        "без ?v= браузер не узнает о правке: у статики Cache-Control: immutable"
    )
    assert "@media (max-width: 834px) {" in _styles() and "@media (max-width: 768px) {" in _styles()


# ── Шаг 9: мелкие правки остатка аудита ──────────────────────────────────────

CALENDAR_LIB = TEMPLATE.parent / "partials" / "cycle_calendar_lib.html"


def _theme_tokens(css: str, opener: str) -> dict[str, str]:
    block = css[css.index(opener):]
    block = block[:block.index("\n}")]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})\s*;", block))


def _contrast(fg: str, bg: str) -> float:
    def lum(hex_color: str) -> float:
        channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("opener", [":root {", ':root[data-theme="dark"] {'])
def test_calendar_days_readable_on_side_panel(opener):
    # 29.09.2026, повторный аудит: в тёмной теме дни недели и номера дней обоих
    # календарей пробников (`--dim` на `--surface-2`) — 4.15 при норме 4.5.
    # Чинится сам токен темы: тот же цвет на той же подложке — ещё сотня мест.
    tokens = _theme_tokens(BASE_CSS.read_text(encoding="utf-8"), opener)
    assert _contrast(tokens["dim"], tokens["surface-2"]) >= 4.5
    for source, selectors in (
        (_styles(), (".mock-weekday", ".mock-day")),
        (CALENDAR_LIB.read_text(encoding="utf-8"), (".cal-weekday", ".cal-day")),
    ):
        for selector in selectors:
            assert "color: var(--dim)" in _css_rule(source, selector), f"{selector} ушёл с --dim — пересчитать контраст"


def test_pale_fills_follow_the_theme():
    # 29.09.2026, повторный аудит: ~25 бледных заливок и рамок плашек были вбиты
    # числом (`rgba(220,38,38,.06)` под `var(--error)` и т. п.) и не менялись с темой.
    # Теперь `color-mix(in srgb, var(--тот же токен, что у текста) N%, transparent)`.
    # Остаются числом: тени, затемнение под окном, белое на фиолетовой шапке
    # и почти сплошные заливки балла под белым текстом — от темы они не зависят.
    styles = _styles()
    pale = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", styles):
        if selector.strip() == ".score-none":  # тёмная подложка балла поверх фото
            continue
        for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]*rgba\([^;]*)", body):
            if prop == "box-shadow" or not (prop.startswith("background") or prop.startswith("border")):
                continue
            for r, g, b, a in re.findall(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)", value):
                if (r, g, b) != ("255", "255", "255") and float(a) < 0.5:
                    pale.append(f"{selector.strip()} {prop}: {value.strip()}")
    assert not pale, "бледная заливка числом — брать color-mix от токена:\n" + "\n".join(pale)
    assert "color-mix(in srgb, var(--text) 5%, transparent)" in _css_rule(styles, ".student-info-pill")


def test_ink_on_pale_fill_is_darker_than_the_fill():
    # Чистый токен текстом на бледной подложке того же токена в светлой теме —
    # 3.1–4.4 при норме 4.5 («Закрыто», «Ждёт проверки», счётчики пробников, фильтры).
    # Рецепт `.profile-badge.no`: токен с 18 % --text; фиолетовый — --blue-on-soft.
    sources = (_styles(), CALENDAR_LIB.read_text(encoding="utf-8"))
    bare = []
    for source in sources:
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
            fill = re.search(r"background:\s*color-mix\(in srgb, var\(--([\w-]+)\) (\d+)%, transparent\)", body)
            if fill and fill.group(1) != "text" and int(fill.group(2)) < 50 and re.search(r"(?<![-\w])color:\s*var\(--%s\)" % fill.group(1), body):
                bare.append(selector.strip().splitlines()[-1])
    assert not bare, "текст чистым токеном на подложке того же токена:\n" + "\n".join(bare)


def test_mock_day_is_not_a_frame_inside_the_subject_card():
    # 29.09.2026, повторный аудит: в «Пробниках» рамка в рамке — предмет → день → фото.
    # Рамку и фон снимаем у дня: его и так отделяют заголовок с датой, календарь
    # (своя подложка) и форма оценки (своя плашка).
    day = _css_rule(_styles(), ".mock-day-card")
    assert "border:" not in day and "background:" not in day
    assert "border:" in _css_rule(_styles(), ".subject-card"), "рамка предмета остаётся"
    assert "background: var(--surface-2)" in _css_rule(_styles(), ".score-form"), "форма оценки отделена подложкой"
