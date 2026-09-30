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
