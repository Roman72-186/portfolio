"""Ученик видит только «Преподавателя» (владелец 05.10.2026: «ученик НИКОГДА
НЕ ДОЛЖЕН ЗНАТЬ, ЧТО ЕМУ ПИШЕТ КТО-ТО КРОМЕ ПРЕПОДАВАТЕЛЯ»). Инвариант —
`docs/invariants/feedback.md`.

Сторож по исходникам экранов, которые открывает только ученик: ни куратора,
ни Главного преподавателя, ни суперадмина, ни модератора в тексте страницы.
Jinja-комментарии `{# #}` не в счёт — их ученик не видит. Подписи в диалогах
ОС держит `services/feedback.py::dialog_sender` и его тесты."""

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

# Шаблоны ученических роутов (`require_student`) и экран видеоуроков.
STUDENT_TEMPLATES = [
    "cabinet_cycle.html",
    "cabinet_learning.html",
    "cabinet_learning_archive.html",
    "cabinet_notifications.html",
    "cabinet_personal.html",
    "cabinet_personal_contacts.html",
    "cabinet_portfolio.html",
    "cabinet_student.html",
    "cabinet_tracker.html",
    "cabinet_videos.html",
]

STAFF_ROLE = re.compile(
    r"куратор|главн\w* преподават|суперадмин|модератор|\bГП\b", re.IGNORECASE,
)


@pytest.mark.parametrize("name", STUDENT_TEMPLATES)
def test_student_screen_names_no_staff_role(name):
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    visible = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    found = sorted({m.group(0) for m in STAFF_ROLE.finditer(visible)})
    assert not found, f"{name}: ученик видит {found} – пишите «преподаватель»"
