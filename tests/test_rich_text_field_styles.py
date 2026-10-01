"""Редактор форматированного текста не наследует оформление подписи поля.

Созвон 30.09.2026: в конструкторе ленты набранный текст выходил заглавными и
жирным, и «Ж» его не меняла. `rich-text-field.js` вставляет редактор внутрь
родителя textarea — в конструкторе это `<label class="prg-field">` с капслоком
и весом 700, — а у `.rt-editable` стоял `font: inherit`.
"""

import pathlib
import re

CSS = pathlib.Path("app/static/css/rich-text-field.css")
TEMPLATES = pathlib.Path("app/templates")


def _rule(source: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", source)
    assert match, f"нет правила {selector}"
    return match.group(1)


def test_editor_sets_its_own_font_instead_of_inheriting_the_label():
    rule = _rule(CSS.read_text(encoding="utf-8"), ".rt-editable")
    assert "font: inherit" not in rule, "редактор снова наследует жирный капслок подписи"
    assert "text-transform: none" in rule
    assert "font-weight: 400" in rule
    assert "letter-spacing: normal" in rule
    assert "font-size: 14px" in rule


def test_editor_is_16px_on_phone():
    source = CSS.read_text(encoding="utf-8")
    phone = source[source.index("@media (max-width: 768px)"):]
    assert re.search(r"\.rt-editable\s*\{\s*font-size: 16px;", phone), (
        "мельче 16px айфон приближает страницу при касании поля"
    )


def test_all_pages_load_the_same_version_of_editor_styles():
    versions = set()
    for template in TEMPLATES.rglob("*.html"):
        versions.update(re.findall(r"rich-text-field\.css\?v=(\d+)", template.read_text(encoding="utf-8")))
    assert len(versions) == 1, f"страницы грузят разные версии rich-text-field.css: {sorted(versions)}"
