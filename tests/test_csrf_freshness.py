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

Плюс сторож на потерянное сообщение: отказ на загрузке фото должен доходить до
ученика текстом сервера, а не общей заглушкой.
"""
import re
from pathlib import Path

from app.config import settings
from app.csrf import generate_csrf_token, validate_csrf_token


STATIC = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"


def test_token_lives_as_long_as_the_session():
    """Токен не может истечь раньше сессии, которой он принадлежит.

    Прежние 6 часов — половина причины прод-инцидента 26.09.2026. Проверяем
    поведением, а не числом: токен, выписанный «сейчас», обязан проходить
    проверку при любом сроке сессии из настроек.
    """
    from app import csrf

    assert csrf._max_age() == settings.session_ttl_hours * 3600
    assert csrf._max_age() >= 24 * 3600, "сессия живёт сутки — токен не должен умирать раньше"


def test_max_age_follows_settings(monkeypatch):
    """Срок читается из настроек на каждой проверке, не при импорте модуля.

    Иначе прод с другим `SESSION_TTL_HOURS` получил бы значение, снятое до
    чтения `.env`, и расхождение нашлось бы только по жалобам учеников.
    """
    from app import csrf

    monkeypatch.setattr(settings, "session_ttl_hours", 72)
    assert csrf._max_age() == 72 * 3600


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
    """Запрос ключа продлевает сессию — путь стоит в `_FORCE_SESSION_REFRESH_PATHS`.

    Это единственный момент, когда точно известно, что человек за вкладкой
    работает: он нажал «Отправить работу». Без продления долгая вкладка теряет
    cookie ровно посреди загрузки фото.
    """
    from app.main import _FORCE_SESSION_REFRESH_PATHS
    from app.models.session import Session as DbSession

    assert "/csrf" in _FORCE_SESSION_REFRESH_PATHS

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


def test_upload_uses_fresh_token_and_asks_for_json():
    """Отправка работы в блоке идёт через общий хелпер, а не сырым fetch.

    Два условия сразу. Свежий ключ — иначе вернётся отказ протухшего. И
    `Accept: application/json` (его ставит хелпер): без него обработчик 403 в
    `app/main.py` отдаёт HTML-страницу «Нет доступа», разбор ответа падает, и до
    ученика не доходит ни причина, ни совет обновить страницу.
    """
    source = (STATIC / "task-blocks-render.js").read_text(encoding="utf-8")

    assert "window.csrfFetch" in source, "хелпер свежего ключа не подключён"
    upload_call = re.search(
        r"post\(block\.upload_endpoint,\s*\{[^}]*body: data", source, re.S
    )
    assert upload_call, "загрузка фото должна идти через post() со свежим ключом"

    # Ни одна мутация блока не должна ставить ключ руками: такой запрос уедет
    # с тем, что было в разметке при отрисовке страницы.
    hand_written = re.findall(r"'X-CSRF-Token':\s*csrfToken", source)
    assert len(hand_written) == 1, (
        "ключ руками ставится только в фолбэке post() для страниц без base.html, "
        f"нашлось мест: {len(hand_written)}"
    )


def test_upload_failure_shows_server_message():
    """Причина отказа доходит до ученика, а не тонет в общей заглушке.

    Сервер отвечает по-разному: свои проверки кладут текст в `error`,
    HTTPException — в `detail`. Код читал только первое, поэтому «Неверный
    CSRF-токен. Обнови страницу» превращалось в «Попробуй ещё раз».
    """
    source = (STATIC / "task-blocks-render.js").read_text(encoding="utf-8")
    assert "body.error || body.detail" in source

    helper = (STATIC / "csrf.js").read_text(encoding="utf-8")
    assert "body.error || body.detail" in helper


def test_csrf_helper_is_loaded_for_every_page():
    """Хелпер подключён в base.html: ключ старится у любой вкладки, не только у формы с фото."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert re.search(r'<script src="/static/js/csrf\.js\?v=\d+"></script>', base)
    assert 'name="csrf-token"' in base, "нет запасного ключа в разметке"


def test_portfolio_and_curator_uploads_refresh_the_token():
    """Оба экрана с XHR-прогрессом берут свежий ключ перед отправкой.

    XHR оставлен вместо fetch ради процента загрузки, поэтому `csrfFetch` им не
    подходит — но ключ они обязаны брать тем же способом.
    """
    for name in ("upload.html", "cabinet_students.html"):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "window.csrfFresh" in source, f"{name} отправляет фото со старым ключом"


PROGRAM_SCREENS = (
    "cabinet_program_cycle_items.html",
    "cabinet_program_cycles.html",
    "cabinet_program_day.html",
    "cabinet_program_stages.html",
)


def test_program_screens_send_fresh_token():
    """Конструктор программы сохраняет со свежим ключом, а не с ключом из разметки.

    Экраны программы ГП и суперадмин держат открытыми часами, а после
    29.09.2026 «Задания цикла» перестали перечитывать страницу даже на
    стрелках. Перезаход в другой вкладке обесценивает ключ страницы, и
    «Сохранить» получало 403 — тот же класс, что инцидент 26.09.2026 у
    учеников. Ключ руками не вшивается ни в заголовок, ни в поле формы.
    """
    for name in PROGRAM_SCREENS:
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "window.csrfFetch" in source, f"{name}: мутации не через csrfFetch"
        assert not re.search(r"'X-CSRF-Token'\s*:\s*csrfToken", source), (
            f"{name}: ключ из разметки вшит в заголовок запроса"
        )
        assert "append('csrf_token', csrfToken)" not in source, (
            f"{name}: ключ из разметки вшит в поле формы"
        )


def test_shared_program_modules_send_fresh_token():
    """Общие модули конструктора тоже берут свежий ключ.

    Запись голоса (`media-recorder-field.js`) живёт ещё и в переписке куратора,
    тренажёр диагностики — на обоих конструкторах. Ключ из разметки у обоих
    остаётся только запасным путём для страниц без base.html.
    """
    recorder = (STATIC / "media-recorder-field.js").read_text(encoding="utf-8")
    assert "window.csrfFresh" in recorder, "запись голоса отправляется со старым ключом"
    assert "append('csrf_token', csrf)" not in recorder

    trainer = (STATIC / "diagnostic-trainer.js").read_text(encoding="utf-8")
    assert "window.csrfFetch" in trainer, "тренажёр отправляет ответы со старым ключом"
    assert "result.body.error || result.body.detail" in trainer, (
        "отказ CSRF кладёт причину в detail — без него человек не видит «обнови страницу»"
    )
