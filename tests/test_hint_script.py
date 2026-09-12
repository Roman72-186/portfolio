"""Скрипт подсказок-«всплывашек» не должен звать функции, которых нет.

Тот же класс сторожа, что `tests/test_program_day_script.py`, но для
`app/static/js/hint.js` — первого в проекте отдельного JS-файла с
popup-механикой (не инлайн-скрипта в шаблоне). Правило проекта (`AGENTS.md`,
правило 11): зелёный `pytest` и код 200 не ловят вырезанную функцию, вызов
которой остался, — ловит только статический разбор объявлений/вызовов или
реальный разбор синтаксиса.
"""

import pathlib
import re
import shutil
import subprocess

import pytest

HINT_JS_PATH = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "hint.js"

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


def test_hint_js_calls_only_defined_functions():
    raw = HINT_JS_PATH.read_text(encoding="utf-8")
    js = _strip_noise(raw)

    declared = set(re.findall(r"function\s+(\w+)", js))
    declared |= set(re.findall(r"\bvar\s+(\w+)", js))
    declared |= set(re.findall(r"\b(\w+)\s*=\s*function", js))
    for params in re.findall(r"function[^(]*\(([^)]*)\)", js):
        declared |= {p.strip() for p in params.split(",") if p.strip()}

    called = set(re.findall(r"(?<![.\w$])([A-Za-z_$]\w*)\s*\(", js))
    missing = sorted(called - declared - KNOWN_GLOBALS)

    assert not missing, f"вызовы без определения: {missing}"


def test_hint_js_is_valid_javascript():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js не установлен — синтаксис скрипта не проверить")

    check = subprocess.run(
        [node, "--check", str(HINT_JS_PATH)], capture_output=True, text=True
    )
    assert check.returncode == 0, f"hint.js не разбирается: {check.stderr}"
