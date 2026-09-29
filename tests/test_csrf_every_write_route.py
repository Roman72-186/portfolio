"""Каждый маршрут на запись проверяет CSRF-ключ — сторож по всему приложению.

Код-ревью 28.09.2026 (P3): из 192 маршрутов на запись без ключа жили шесть
служебных — два срока в конструкторе программы, два переноса и два удаления
работ ученика. Их прикрывала только `SameSite=lax` у сессионной cookie: защита
про запас, но держалась она на настройке браузера, а не на коде. Нашлись они
грепом по одному, и следующий забытый маршрут нашёлся бы так же — случайно.

Тест обходит `app.routes` и требует у каждого POST/PUT/PATCH/DELETE одну из
зависимостей проверки ключа. Исключения — закрытый список ниже, у каждого
причина. Новый маршрут без ключа краснеет здесь, а не на ревью через месяц.

Фикстура `client` в `conftest.py` проверку ключа выключает, поэтому сам отказ
403 проверяется отдельно — с настоящей зависимостью.
"""

import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from app.api.guest_exam import require_guest_csrf
from app.dependencies import require_csrf, require_csrf_header
from app.main import app

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_DEPENDENCIES = (require_csrf, require_csrf_header, require_guest_csrf)
TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

# Путь → почему ключа нет. Добавлять сюда только маршрут, у которого на момент
# запроса нет сессии, к которой ключ привязан, или который зовёт не браузер.
NO_CSRF_BY_DESIGN = {
    "/login": "сессии ещё нет — ключ не к чему привязать",
    "/logout": "выход; прикрыт SameSite=lax, подделка только разлогинит",
    "/auth/telegram/webhook": "зовёт Telegram, сверка секрета в заголовке",
    "/auth/internal/issue-link": "межсервисный вызов по внутреннему ключу",
    "/auth/internal/sso/verify": "межсервисный вызов по внутреннему ключу",
    "/cabinet/superadmin/impersonate/stop": (
        "аварийный выход по подписанной cookie имперсонации — работает, даже "
        "когда сессия подменённого пользователя недействительна"
    ),
    "/guest/{token}/start": "гостевой cookie ещё нет — ключ не к чему привязать",
}


def _checks_csrf(dependant) -> bool:
    return any(
        dep.call in CSRF_DEPENDENCIES or _checks_csrf(dep)
        for dep in dependant.dependencies
    )


def _write_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.methods & WRITE_METHODS:
            yield route


def test_every_write_route_checks_csrf():
    missing = sorted(
        f"{'/'.join(sorted(route.methods & WRITE_METHODS))} {route.path}"
        for route in _write_routes()
        if route.path not in NO_CSRF_BY_DESIGN and not _checks_csrf(route.dependant)
    )
    assert not missing, (
        "маршруты записи без CSRF-ключа (добавь Depends(require_csrf_header) "
        "или, если ключа быть не может, строку с причиной в NO_CSRF_BY_DESIGN):\n"
        + "\n".join(missing)
    )


def test_exemptions_are_still_real_routes():
    """Исключение для удалённого маршрута — дыра наготове: новый маршрут по
    тому же адресу молча пройдёт без ключа."""
    paths = {route.path for route in _write_routes()}
    assert not set(NO_CSRF_BY_DESIGN) - paths


@pytest.mark.parametrize(
    "method, url, body",
    [
        ("DELETE", "/cabinet/students/{sid}/works/bulk",
         {"work_type": "after", "month": "Сентябрь", "year": 2026}),
        ("PATCH", "/cabinet/students/{sid}/portfolio/month",
         {"from_month": "Сентябрь", "to_month": "Октябрь", "year": 2026}),
        ("PATCH", "/cabinet/students/{sid}/portfolio/works/1/move",
         {"to_month": "Октябрь", "year": 2026}),
        ("DELETE", "/cabinet/students/{sid}/works/1", None),
        ("POST", "/cabinet/staff/program/blocks/1/deadline",
         {"submit_until": None, "submit_deadlines": []}),
        ("POST", "/cabinet/staff/program/items/1/deadline",
         {"submit_until": None, "submit_deadlines": []}),
    ],
)
def test_service_routes_refuse_request_without_token(
    admin_client, regular_user, method, url, body
):
    """Шесть маршрутов из ревью отвечают 403 без ключа — до поиска записи и
    разбора тела, поэтому несуществующие id здесь не мешают."""
    client, _ = admin_client  # суперадмин: проходит роль на всех шести
    header_override = app.dependency_overrides.pop(require_csrf_header)
    try:
        resp = client.request(
            method,
            url.format(sid=regular_user.id),
            json=body,
            headers={"Accept": "application/json"},
        )
    finally:
        app.dependency_overrides[require_csrf_header] = header_override

    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize(
    "name", ["cabinet_admin_mock_check.html", "cabinet_admin_retake_check.html"]
)
def test_check_screens_delete_work_with_fresh_token(name):
    """Удаление работы с экранов проверки пробника и пересдачи идёт через
    `csrfFetch`: без ключа маршрут теперь отвечает 403."""
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    call = re.search(r"function deleteWork\([^)]*\)\s*\{(.*?)\n\}", source, re.S)
    assert call, f"{name}: не нашёл deleteWork"
    assert "window.csrfFetch('/cabinet/students/'" in call.group(1), (
        f"{name}: удаление работы уходит без CSRF-ключа"
    )
