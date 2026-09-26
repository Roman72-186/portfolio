"""CSRF protection via itsdangerous signed tokens.

Token = URLSafeTimedSerializer.dumps(session_id) signed with session_secret.
Tied to the user's session — invalidated on logout.
"""
from itsdangerous import URLSafeTimedSerializer, BadData

from app.config import settings

def _max_age() -> int:
    """Срок жизни токена равен сроку жизни сессии (`session_ttl_hours`).

    Держать его короче бессмысленно: токен привязан к `session_id`, и когда
    сессия кончается, запрос отбивает 401 ещё до проверки CSRF. Прежние 6 часов
    делали ровно одну вещь — ломали долго открытую вкладку ученика: за 20 часов
    25-26.09.2026 прод отдал 141 отказ «Неверный CSRF-токен» на живых сессиях,
    в том числе на отправке работы в задании (`/cabinet/tracker/blocks/*/upload`).
    Читается из настроек на каждой проверке, а не при импорте, — иначе тест или
    прод с другим `SESSION_TTL_HOURS` получил бы значение, снятое до чтения .env.

    Второй слой защиты от протухания — свежий токен перед отправкой
    (`GET /csrf`, фронт берёт его в `app/static/js/csrf.js`).
    """
    return settings.session_ttl_hours * 3600


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="csrf-v1")


def generate_csrf_token(session_id: str) -> str:
    return _serializer().dumps(session_id)


def validate_csrf_token(session_id: str, token: str) -> bool:
    if not token or not session_id:
        return False
    try:
        value = _serializer().loads(token, max_age=_max_age())
        return value == session_id
    except BadData:
        return False
