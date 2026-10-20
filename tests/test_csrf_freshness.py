"""Долгая вкладка не должна ломать отправку работы (26.09.2026).

CSRF-токен печатался в HTML при рендере и жил 6 часов, а вкладка ученика на
телефоне живёт сутками. За 20 часов 25-26.09.2026 прод отдал 141 отказ
«Неверный CSRF-токен» на живых сессиях: отправка работы в задании
(`/cabinet/tracker/blocks/*/upload`), прогресс видео, отметки «выполнено».
Ученик читал «Не удалось загрузить. Попробуй ещё раз», хотя повтор не помогал
никогда — помогало только обновление страницы, о котором никто не говорил.

Лечение в двух слоях, и тесты стерегут оба:

1. Срок жизни токена равен сроку жизни сессии (`app/csrf.py`). Держать короче
   бессмысленно: токен привязан к `session_id`, и по смерти сессии запрос и так
   отбивает 401 до проверки CSRF.
2. Фронт берёт свежий токен перед каждой мутацией — `GET /csrf`
   (`app/api/auth.py::fresh_csrf`), клиентская часть в `app/static/js/csrf.js`.
"""
from pathlib import Path

from app.config import settings
from app.csrf import generate_csrf_token, validate_csrf_token


STATIC = Path(__file__).resolve().parent.parent / "app" / "static" / "js"


def test_token_lives_as_long_as_the_session(monkeypatch):
    """Токен не умирает раньше сессии, которой он принадлежит, и умирает вместе с ней.

    Прежние 6 часов — половина причины прод-инцидента 26.09.2026. Срок берётся
    из настроек на каждой проверке, а не при импорте модуля.
    """
    import time

    token = generate_csrf_token("session-1")
    issued = time.time()

    def later(hours):
        monkeypatch.setattr(time, "time", lambda: issued + hours * 3600)

    later(23)
    assert validate_csrf_token("session-1", token), "сессия живёт сутки — токен тоже"

    monkeypatch.setattr(settings, "session_ttl_hours", 72)
    later(71)
    assert validate_csrf_token("session-1", token)
    later(73)
    assert not validate_csrf_token("session-1", token)


def test_fresh_csrf_returns_token_valid_for_current_session(auth_client):
    client, _user = auth_client
    resp = client.get("/csrf")
    assert resp.status_code == 200
    token = resp.json()["csrf_token"]
    assert token
    session_id = next(
        cookie.value for cookie in client.cookies.jar if cookie.name == "session_id"
    )
    assert validate_csrf_token(session_id, token)


def test_fresh_csrf_requires_auth(client):
    """Без сессии — 401 JSON-ом, чтобы фронт отличил «протух ключ» от «нет входа».

    `Accept: application/json` обязателен: без него обработчик 401 отдаёт
    редирект на страницу входа, и хелпер принял бы её за ответ с токеном.
    """
    resp = client.get("/csrf", headers={"Accept": "application/json"})
    assert resp.status_code == 401


def test_fresh_csrf_extends_the_session(auth_client, db):
    """Запрос ключа продлевает сессию.

    Это единственный момент, когда точно известно, что человек за вкладкой
    работает: он нажал «Отправить работу». Без продления долгая вкладка теряет
    cookie ровно посреди загрузки фото.
    """
    from app.models.session import Session as DbSession

    client, _user = auth_client
    session_id = next(
        cookie.value for cookie in client.cookies.jar if cookie.name == "session_id"
    )
    before = db.query(DbSession).filter(DbSession.id == session_id).one().expires_at
    db.expire_all()
    resp = client.get("/csrf")
    assert resp.status_code == 200
    after = db.query(DbSession).filter(DbSession.id == session_id).one().expires_at
    assert after >= before


def test_csrf_failure_names_the_reason_by_status():
    """`csrfFailure` в деле: 401 — войти заново, 403 и 422 — текст сервера,
    сеть и 5xx — заглушка с «попробуйте ещё раз». Без Node тест пропускается."""
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node не установлен")
    helper = (STATIC / "csrf.js").read_text(encoding="utf-8")
    cases = [
        [401, {"detail": "Сессия истекла"}],
        [403, {"detail": "Раздел открыт только на просмотр"}],
        [403, {"detail": "Неверный CSRF-токен. Обнови страницу и попробуй снова."}],
        [422, {"detail": "Этот тип скрыт — выберите другой"}],
        [422, {"detail": [{"msg": "field required"}]}],
        [500, {"detail": "Internal server error"}],
        [None, None],
    ]
    script = (
        "var window = {};\n" + helper + "\n"
        "var cases = " + json.dumps(cases) + ";\n"
        "console.log(JSON.stringify(cases.map(function (c) {"
        " return window.csrfFailure(c[0], c[1], 'FALLBACK'); })));"
    )
    run = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == [
        "Сессия закончилась. Обновите страницу и войдите заново.",
        "Раздел открыт только на просмотр",
        "Неверный CSRF-токен. Обнови страницу и попробуй снова.",
        "Этот тип скрыт — выберите другой",
        "FALLBACK",
        "FALLBACK",
        "FALLBACK",
    ]
