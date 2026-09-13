"""Маска телефона не должна звать функций, которых нет, и обязана подключаться.

Тот же класс сторожа, что `tests/test_hint_script.py`, но для
`app/static/js/phone-mask.js`. Правило проекта (`AGENTS.md`, правило 11):
зелёный `pytest` и код 200 не ловят вырезанную функцию, вызов которой остался, —
ловит только статический разбор объявлений и вызовов либо разбор синтаксиса.

Вторая половина файла — про поштучный деплой (`AGENTS.md`, критичное правило 4):
скрипт и оба шаблона, которые его зовут, ездят на прод вместе. Тест держит эту
связку: если из шаблона убрали `data-phone-mask` или тег `<script>`, поле молча
вернётся к свободному вводу, а страница останется рабочей на вид.
"""

import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PHONE_MASK_JS_PATH = ROOT / "app" / "static" / "js" / "phone-mask.js"
TEMPLATES_WITH_MASK = [
    ROOT / "app" / "templates" / "profile.html",
    ROOT / "app" / "templates" / "cabinet_personal_contacts.html",
]

KNOWN_GLOBALS = {
    "if", "for", "while", "switch", "catch", "function", "return", "typeof",
    "new", "in", "of", "do", "else", "try", "throw", "delete", "void",
    "Array", "Object", "JSON", "String", "Number", "Boolean", "Promise",
    "Date", "Math", "Set", "Map", "RegExp", "Error",
    "fetch", "parseInt", "parseFloat", "isNaN", "isFinite",
    "setTimeout", "clearTimeout", "setInterval", "clearInterval",
    "console",
}


def _strip_noise(js: str) -> str:
    """Убрать блочные и строчные комментарии и строковые литералы."""
    js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
    js = re.sub(r"(?m)^\s*//.*$", " ", js)
    js = re.sub(r"//[^\n'\"]*$", " ", js, flags=re.M)
    js = re.sub(r"'(?:\\.|[^'\\])*'", "''", js)
    js = re.sub(r'"(?:\\.|[^"\\])*"', '""', js)
    return js


def test_phone_mask_js_calls_only_defined_functions():
    raw = PHONE_MASK_JS_PATH.read_text(encoding="utf-8")
    js = _strip_noise(raw)

    declared = set(re.findall(r"function\s+(\w+)", js))
    declared |= set(re.findall(r"\bvar\s+(\w+)", js))
    declared |= set(re.findall(r"\b(\w+)\s*=\s*function", js))
    for params in re.findall(r"function[^(]*\(([^)]*)\)", js):
        declared |= {p.strip() for p in params.split(",") if p.strip()}

    called = set(re.findall(r"(?<![.\w$])([A-Za-z_$]\w*)\s*\(", js))
    missing = sorted(called - declared - KNOWN_GLOBALS)

    assert not missing, f"вызовы без определения: {missing}"


def test_phone_mask_js_is_valid_javascript():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js не установлен — синтаксис скрипта не проверить")

    check = subprocess.run(
        [node, "--check", str(PHONE_MASK_JS_PATH)], capture_output=True, text=True
    )
    assert check.returncode == 0, f"phone-mask.js не разбирается: {check.stderr}"


@pytest.mark.parametrize("template", TEMPLATES_WITH_MASK, ids=lambda p: p.name)
def test_template_podklyuchaet_masku_i_pomechaet_polya(template):
    html = template.read_text(encoding="utf-8")
    assert "/static/js/phone-mask.js" in html, "шаблон не подключает скрипт маски"
    assert html.count("data-phone-mask") == 2, "размечены не оба поля телефона"
    # `Cache-Control: immutable` на статике: ссылка без `?v=<число>` отдаст
    # ученику старый файл до ручной чистки кэша (та же проверка, что фикстура
    # `assert_static_versioned` делает для отрисованных страниц).
    assert re.search(r'/static/js/phone-mask\.js\?v=\d+"', html), "ссылка на скрипт без версии"
