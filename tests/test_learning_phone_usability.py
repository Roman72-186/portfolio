"""Лента обучения ученика с телефона (аудит 30.09.2026, план `plans/2026-09-30-apparchi-student-aop-audit.md`).

Сторожа тех правок аудита, которые видно без браузера. Замеры, ради которых
они сделаны, сняты живым прогоном стенда на 320–1280 px:

- «Назад» на первом вопросе опроса должна быть скрыта, но `.btn-outline`
  перебивал атрибут `hidden` — нажатие стирало опрос до перезагрузки.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
BASE_CSS = STATIC / "css" / "base.css"


def test_hidden_attribute_beats_any_display():
    """Находка 1: `hidden` прячет элемент, какой бы `display` ни дал ему класс.

    Браузерное `[hidden] { display: none }` слабее любого авторского правила,
    поэтому общий слой обязан держать своё — с `!important`, иначе скрытая
    кнопка с классом `.btn-outline` видна и нажимается.
    """
    css = BASE_CSS.read_text(encoding="utf-8")
    assert re.search(r"(^|\n)\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}", css), (
        "в base.css нет общего `[hidden] { display: none !important; }`"
    )


TRACKER_CSS = STATIC / "css" / "tracker.css"


def _hex_luminance(value: str) -> float:
    value = value.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(value[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_hex_luminance(a), _hex_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_done_marks_are_readable_on_bright_green():
    """Находка 4: «СДЕЛАНО» и «ЗАДАНИЕ ВЫПОЛНЕНО» — белое на `--ios-green` давало
    2.2 / 2.0. Владелец 01.10.2026 выбрал «яркий зелёный, тёмные буквы»: заливка
    прежняя, буквы `--on-ios-green`, ≥ 4.5 в обеих темах."""
    base = BASE_CSS.read_text(encoding="utf-8")
    greens = re.findall(r"--ios-green:\s*(#[0-9A-Fa-f]{6})", base)
    assert len(greens) == 2, "ждём --ios-green светлой и тёмной темы"
    ink = re.search(r"--on-ios-green:\s*(#[0-9A-Fa-f]{6})", base).group(1)
    for green in greens:
        assert _contrast(green, ink) >= 4.5, f"буквы {ink} на {green}: {_contrast(green, ink):.2f}"

    tracker = TRACKER_CSS.read_text(encoding="utf-8")
    for selector in (".trk-badge--done", ".ios-learning .trk-badge--done", ".ios-learning .trk-toggle-btn.is-done"):
        rule = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", tracker).group(1)
        assert "color: var(--on-ios-green)" in rule, selector


def test_done_step_is_dimmed_by_title_not_by_opacity():
    """Находка 4: `opacity: 0.6` у выполненного шага гасила и зелёную плашку внутри
    (1.6 / 1.5). Приглушает серый заголовок, прозрачности у карточки нет."""
    tracker = TRACKER_CSS.read_text(encoding="utf-8")
    assert not re.search(r"\.lrn-step--done\s*\{[^}]*opacity", tracker)
    assert ".lrn-step--done .lrn-step-title { color: var(--ios-label-2); }" in tracker


def test_done_step_opens_by_tap_not_by_hover():
    """Находка 5: тело выполненного шага раскрывалось только наведением
    (`.lrn-step--done:hover`). На телефоне наведения нет: Chromium случайно
    считает им касание, iPhone — через раз, а касание мимо снова сворачивает.
    Теперь раскрывает кнопка «Показать» классом `.is-open`; цель касания ≥ 44px."""
    tracker = TRACKER_CSS.read_text(encoding="utf-8")
    assert ":hover .lrn-step-body" not in tracker
    assert ".lrn-step--done.is-open .lrn-step-body { display: block; }" in tracker
    rule = re.search(r"\.lrn-step-toggle\s*\{([^}]*)\}", tracker).group(1)
    assert "min-height: 44px" in rule


def test_locked_step_explains_itself_readably():
    """Находка 6: `opacity: 0.5` у запертой карточки гасила и объяснение,
    почему закрыто: «Откроется, когда будет сделано предыдущее» — 2.14 / 2.48,
    заголовок 3.55. Запертый шаг отличает пунктирная рамка, а текст — серый
    `--ios-label-2` без прозрачности (≥ 4.5 на карточке в обеих темах)."""
    tracker = TRACKER_CSS.read_text(encoding="utf-8")
    assert not re.search(r"\.lrn-step--locked\s*\{[^}]*opacity", tracker)
    assert ".lrn-step--locked .lrn-step-title { color: var(--ios-label-2); }" in tracker
    assert ".ios-learning .lrn-card-note { font: 400 13px/1.4 var(--ios-font); color: var(--ios-label-2); }" in tracker
    base = BASE_CSS.read_text(encoding="utf-8")
    light_label = re.search(r"--ios-label-2:\s*(#[0-9A-Fa-f]{6})", base).group(1)
    light_card = re.search(r"--surface:\s*(#[0-9A-Fa-f]{6})", base).group(1)
    assert _contrast(light_label, light_card) >= 4.5


def test_file_picker_speaks_the_screen_language():
    """Находка 7: поле выбора файла было системным — «Choose Files / No file
    chosen» на языке телефона, не в стиле экрана. Теперь нажимают на кнопку
    `label` «Выбрать фото», имена выбранных файлов видны строкой под ней.
    Само поле остаётся настоящим и скрыто только визуально: `display: none`
    убрал бы его из фокуса с клавиатуры и из экранного диктора."""
    render = (STATIC / "js" / "task-blocks-render.js").read_text(encoding="utf-8")
    assert "el('input', 'file-pick-input')" in render
    assert "el('label', 'btn-outline file-pick-btn', 'Выбрать фото')" in render
    assert "'file-pick-names'" in render

    base = BASE_CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.file-pick-input\s*\{([^}]*)\}", base).group(1)
    assert "display: none" not in rule
    assert "clip-path: inset(50%)" in rule
    assert ".file-pick-input:focus-visible + .file-pick-btn" in base


def test_hint_trigger_catches_a_finger_44px_wide():
    """Находка 9: у «?» видимый кружок 20×20, область касания добирает
    невидимый `::before`. Отступ считается от внутренней границы (без рамки
    в 1px): `inset: -12px` давал 18 + 24 = 42, нужно −13 → 44. В шапке ленты
    низ области съедал абзац описания: у заголовка и абзаца был одинаковый
    `z-index: 1`, абзац позже в разметке и рисуется поверх — заголовок выше."""
    base = BASE_CSS.read_text(encoding="utf-8")
    assert ".hint-trigger::before { content: ''; position: absolute; inset: -13px; }" in base
    assert "width: 20px; height: 20px; border-radius: 50%;" in base  # 20 − 2 рамки + 2 × 13 = 44
    tracker = TRACKER_CSS.read_text(encoding="utf-8")
    h1 = re.search(r"\.ios-learning \.lrn-hero h1\s*\{([^}]*)\}", tracker).group(1)
    p = re.search(r"\.ios-learning \.lrn-hero p\s*\{([^}]*)\}", tracker).group(1)
    z = lambda rule: int(re.search(r"z-index:\s*(\d+)", rule).group(1))
    assert z(h1) > z(p)


def test_subject_colors_are_readable_on_light_card():
    """Находка 10: цвета предметов на белой карточке давали 3.65 (`#0A84FF`
    «Рисунок», `#FF2D55` «Композиция»). Оттенок — решение владельца 26.08.2026,
    меняется только светлота: в светлой теме смесь 86 % цвета с чёрным
    (`color-mix`), в тёмной — исходный цвет (там и так 4.66)."""
    tracker = TRACKER_CSS.read_text(encoding="utf-8")
    hue = {
        "drawing": re.search(r"--subj-drawing-hue:\s*(#[0-9A-Fa-f]{6})", tracker).group(1),
        "composition": re.search(r"--ios-pink:\s*(#[0-9A-Fa-f]{6})", tracker).group(1),
    }
    assert hue == {"drawing": "#0A84FF", "composition": "#FF2D55"}, "оттенки предметов не меняем"
    light = re.search(r"\.ios-learning\s*\{([^}]*)\}", tracker).group(1)
    assert "--subj-drawing: color-mix(in srgb, var(--subj-drawing-hue) 86%, black);" in light
    assert "--subj-composition: color-mix(in srgb, var(--ios-pink) 86%, black);" in light
    dark = re.search(r':root\[data-theme="dark"\] \.ios-learning\s*\{([^}]*)\}', tracker).group(1)
    assert "--subj-drawing: var(--subj-drawing-hue);" in dark
    assert "--subj-composition: var(--ios-pink);" in dark
    for value in hue.values():
        mixed = "#" + "".join(f"{round(int(value[i:i + 2], 16) * 0.86):02X}" for i in (1, 3, 5))
        assert _contrast(mixed, "#FFFFFF") >= 4.5, f"{value} → {mixed}: {_contrast(mixed, '#FFFFFF'):.2f}"
    for subject, token in (("Рисунок", "--subj-drawing"), ("Композиция", "--subj-composition")):
        rule = re.search(r'\.ios-learning \.lrn-subject-btn\[data-subject="' + subject + r'"\]\.active\s*\{([^}]*)\}', tracker).group(1)
        assert f"color: var({token});" in rule


_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S)


def test_feed_code_is_cached_not_inlined(auth_client, assert_static_versioned):
    """Находка 11: плеер, лента, лайтбокс и колокольчик — файлами, а не в странице.

    Встроенными они весили 70 КБ из 95 КБ JS ленты и заново качались при
    каждом заходе: статика кэшируется на год, HTML — нет. После выноса
    (01.10.2026) встроенного JS в ленте 25 КБ — тема до стилей, Telegram,
    меню ученика. Потолок с запасом: новый крупный скрипт пусть ложится файлом.
    """
    client, _ = auth_client
    html = client.get("/cabinet/learning").text
    inline = "".join(_INLINE_SCRIPT.findall(html))

    for src, marker in (
        ("video-player.js", "window.lrnVideoPlayer"),
        ("cycle-feed.js", "lrnBlockRender.el"),
        ("lightbox.js", "getElementById('lightbox-img')"),
        ("notif-bell.js", "getElementById('notifPopBody')"),
    ):
        assert f'src="/static/js/{src}?v=' in html, f"лента не подключает {src}"
        assert marker not in inline, f"код {src} снова встроен в страницу"
        assert marker in (STATIC / "js" / src).read_text(encoding="utf-8")
    assert_static_versioned(html)
    assert len(inline.encode()) < 32 * 1024, f"встроенного JS {len(inline.encode()) // 1024} КБ"


def test_csrf_key_rides_on_the_script_tag():
    """Файл кэшируется на год, а ключ у каждой сессии свой — вшить его в файл нельзя.

    Ключ идёт атрибутом тега и читается сразу, синхронно: внутри
    `DOMContentLoaded` `document.currentScript` уже `null`, и колокольчик
    слал бы пустой ключ.
    """
    for src in ("cycle-feed.js", "notif-bell.js"):
        code = (STATIC / "js" / src).read_text(encoding="utf-8")
        assert "{{" not in code, f"в {src} осталась разметка Jinja"
        head = code.split("addEventListener('DOMContentLoaded'", 1)[0]
        assert "document.currentScript" in head and "data-csrf-token" in head, src


def test_poll_options_and_rules_are_a_finger_tall():
    """Находка 13: варианты опроса и пункты правил были 42px — `padding` 8px
    сверху и снизу плюс строка. Оба рендера (`task-blocks-render.js`, ветки
    опроса и `lrn-blk-rules`) берут один класс `.lrn-blk-option`, поэтому
    норма касания держится на нём — `min-height`, а не отступами: длинный
    вариант и так выше."""
    tracker = TRACKER_CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.lrn-blk-option\s*\{([^}]*)\}", tracker).group(1)
    assert "min-height: 44px" in rule
    render = (STATIC / "js" / "task-blocks-render.js").read_text(encoding="utf-8")
    assert render.count("el('label', 'lrn-blk-option')") >= 2, "опрос и правила рисуются одним классом"


def test_week_tab_styles_are_gone():
    """Находка 14: вкладки недели сняты 06.09.2026, стили `.lrn-tabs*`,
    `.lrn-tab-arrow*`, `.lrn-tab-dot`, `.lrn-tabpanel` остались сиротами —
    разметки под них нет ни в шаблонах, ни в JS. Вкладки предметов
    «Общее / Композиция / Рисунок» — другие классы (`.lrn-subject-*`)."""
    tracker = TRACKER_CSS.read_text(encoding="utf-8")
    assert not re.findall(r"\.lrn-tab[\w-]*", tracker)
    assert ".lrn-subject-btn" in tracker


def _feed_rule_bodies(css: str):
    """Тела правил ленты (`.lrn-*`, `.ios-learning …`) без комментариев;
    сам блок токенов `.ios-learning { --ios-pink: … }` не входит."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        selector = selector.strip().splitlines()[-1].strip()
        if re.search(r"lrn-|ios-learning", selector) and selector != ".ios-learning":
            yield selector, body


def test_feed_colors_come_from_tokens_where_a_token_exists():
    """Находка 15: белое на заливке — `--on-color`, полупрозрачные оранжевый,
    красный и фиолетовый — `color-mix` от `--ios-orange`/`--ios-red`/`--ios-blue`,
    цвета предметов — их токены (оттенок — решение владельца 26.08, не меняется).
    Тени, системные серые iOS и подложки видео токенов не имеют — остаются числом."""
    tracker = TRACKER_CSS.read_text(encoding="utf-8")
    literal = re.compile(r"#fff\b|#ffffff\b|rgba\(\s*(255,\s*149,\s*0|255,\s*59,\s*48|175,\s*82,\s*222)\s*,", re.I)
    offenders = [s for s, body in _feed_rule_bodies(tracker) if literal.search(body)]
    assert not offenders, offenders
    subject = [body for s, body in _feed_rule_bodies(tracker) if s.startswith(".lrn-subject-btn[data-subject=")]
    assert len(subject) == 2 and not any(re.search(r"color:\s*#", b) for b in subject)
