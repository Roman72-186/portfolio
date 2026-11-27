"""Защита сессионных маршрутов записи и сохранение настройки с действительным CSRF-ключом."""


import pytest
from fastapi.routing import APIRoute

from app.dependencies import require_csrf, require_csrf_header
from app.main import app

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Путь → почему ключа нет. Добавлять сюда только маршрут, у которого на момент
# запроса нет сессии, к которой ключ привязан, или который зовёт не браузер.
NO_CSRF_BY_DESIGN = {
    "/login": "сессии ещё нет — ключ не к чему привязать",
    "/telegram-staff": (
        "вход сотрудника ради ссылки на бота, сессию не создаёт; подделка лишь "
        "открыла бы бота тому, кто и так знает логин и пароль"
    ),
    "/logout": "выход; прикрыт SameSite=lax, подделка только разлогинит",
    "/auth/telegram/webhook": "зовёт Telegram, сверка секрета в заголовке",
    "/payments/prodamus/webhook": "зовёт сервер Продамуса, сверка подписи в заголовке Sign",
    "/auth/internal/issue-link": "межсервисный вызов по внутреннему ключу",
    "/internal/telegram-analytics": "межсервисный вызов с Bearer",
    "/lab/event": "проверяет CSRF-токен текущей сессии в JSON",
    "/auth/internal/sso/verify": "межсервисный вызов по внутреннему ключу",
    "/cabinet/superadmin/impersonate/stop": (
        "аварийный выход по подписанной cookie имперсонации — работает, даже "
        "когда сессия подменённого пользователя недействительна"
    ),
    "/guest/{token}/start": "гостевой cookie ещё нет — ключ не к чему привязать",
    "/auth/vpn-request": (
        "экран входа, сессии ещё нет; подделка лишь шлёт ключ VPN в Telegram "
        "хозяину ника — держат лимит по адресу и пауза на ученика"
    ),
}


def _write_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.methods & WRITE_METHODS:
            yield route


def test_every_write_route_rejects_missing_csrf(admin_client):
    """Проверяем зависимости зарегистрированных маршрутов и middleware без выполнения записей."""
    import re
    from fastapi import Depends, FastAPI, Response
    from fastapi.testclient import TestClient
    from app.csrf import generate_csrf_token
    from app.db.database import get_db
    from app.services.guest_exam import GUEST_COOKIE_NAME

    client, _ = admin_client
    session_id = client.cookies.get("session_id")
    token = generate_csrf_token(session_id)
    probe = FastAPI(middleware=app.user_middleware)
    probe.dependency_overrides[get_db] = app.dependency_overrides[get_db]

    async def accepted():
        return Response(status_code=204)

    cases = []
    for route in _write_routes():
        if route.path in NO_CSRF_BY_DESIGN:
            continue
        methods = sorted(route.methods & WRITE_METHODS)
        # Сохраняем весь набор зависимостей. Заглушка заменяет только бизнес-операцию.
        probe.add_api_route(route.path, accepted, methods=methods,
                            dependencies=[Depends(dep.call) for dep in route.dependant.dependencies])
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        cases.extend((method, path) for method in methods)

    with TestClient(probe) as local:
        local.cookies.set("session_id", session_id)
        local.cookies.set(GUEST_COOKIE_NAME, session_id)
        for method, path in cases:
            assert local.request(method, path, follow_redirects=False).status_code == 403, (method, path)
            valid = local.request(method, path, data={"csrf_token": token},
                                  headers={"X-CSRF-Token": token}, follow_redirects=False)
            assert valid.status_code == 204, (method, path, valid.status_code)


def test_csrf_protects_saved_notification_preference(auth_client, db, monkeypatch):
    from app.csrf import generate_csrf_token

    client, user = auth_client
    for guard in (require_csrf, require_csrf_header):
        monkeypatch.delitem(app.dependency_overrides, guard, raising=False)
    user.telegram_chat_id = 555555
    user.telegram_notifications_enabled = True
    db.commit()
    url = "/cabinet/notifications/telegram-toggle"
    data = {"enabled": False}
    assert client.post(url, json=data).status_code == 403
    db.refresh(user)
    assert user.telegram_notifications_enabled is True
    token = generate_csrf_token(client.cookies.get("session_id"))
    assert client.post(url, json=data, headers={"X-CSRF-Token": token}).status_code == 200
    db.refresh(user)
    assert user.telegram_notifications_enabled is False


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
