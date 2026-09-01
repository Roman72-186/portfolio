"""Скрипт страницы дня не должен звать функции, которых нет.

Сторож для конкретной аварии 01.09.2026: при снятии анкеты (`2cec4a4`) вместе
с её кодом уехал соседний блок переключения вкладок, и `showForm`/`hideForms`
остались только в вызовах. Синтаксис при этом валиден, страница отдаётся с
кодом 200, тесты зелёные — а в браузере не работает ни одна плитка.

Проверка статическая: снимаем комментарии и строковые литералы, собираем
объявления и вызовы, сравниваем. Ловит ровно этот класс поломки — «удалили
кусок, вызовы остались».
"""

import re
from datetime import timedelta

from app.services.tz import today_msk

# Всё, что живёт вне скрипта страницы: платформа, браузерные глобальные,
# ключевые слова с круглой скобкой следом.
KNOWN_GLOBALS = {
    "if", "for", "while", "switch", "catch", "function", "return", "typeof",
    "new", "in", "of", "do", "else", "try", "throw", "delete", "void",
    "Array", "Object", "JSON", "String", "Number", "Boolean", "Promise",
    "Date", "Math", "Set", "Map", "RegExp", "Error", "FormData", "URL",
    "URLSearchParams", "Blob", "File", "FileReader", "IntersectionObserver",
    "MutationObserver", "CustomEvent", "Event",
    "fetch", "parseInt", "parseFloat", "isNaN", "isFinite", "encodeURIComponent",
    "decodeURIComponent", "setTimeout", "clearTimeout", "setInterval",
    "clearInterval", "requestAnimationFrame", "alert", "confirm", "prompt",
    "console", "queueMicrotask", "structuredClone", "AbortController",
}


def _strip_noise(js: str) -> str:
    """Убрать блочные и строчные комментарии и строковые литералы."""
    js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
    js = re.sub(r"(?m)^\s*//.*$", " ", js)
    js = re.sub(r"//[^\n'\"]*$", " ", js, flags=re.M)
    js = re.sub(r"'(?:\\.|[^'\\])*'", "''", js)
    js = re.sub(r'"(?:\\.|[^"\\])*"', '""', js)
    return js


def test_day_page_script_calls_only_defined_functions(
    client, db, user_factory, session_factory
):
    admin = user_factory(vk_id=990_101, name="Главный", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    iso = (today_msk() + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{iso}")
    assert page.status_code == 200

    for raw in re.findall(r"<script>(.*?)</script>", page.text, re.S):
        js = _strip_noise(raw)
        declared = set(re.findall(r"function\s+(\w+)", js))
        declared |= set(re.findall(r"\bvar\s+(\w+)", js))
        declared |= set(re.findall(r"\b(\w+)\s*=\s*function", js))
        for params in re.findall(r"function[^(]*\(([^)]*)\)", js):
            declared |= {p.strip() for p in params.split(",") if p.strip()}

        # Вызов вида `name(` без точки перед ним — кандидат в локальную функцию.
        called = set(re.findall(r"(?<![.\w$])([A-Za-z_$]\w*)\s*\(", js))
        missing = sorted(called - declared - KNOWN_GLOBALS)

        assert not missing, f"вызовы без определения: {missing}"
