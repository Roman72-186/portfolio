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
