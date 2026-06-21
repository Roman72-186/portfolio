"""CSRF protection via itsdangerous signed tokens.

Token = URLSafeTimedSerializer.dumps(session_id) signed with session_secret.
Tied to the user's session — invalidated on logout.
"""
from itsdangerous import URLSafeTimedSerializer, BadData

from app.config import settings

# Must outlast the longest single-page flow that reuses a page-load token,
# including the mock exam upload timer plus margin.
_MAX_AGE = 6 * 3600


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="csrf-v1")


def generate_csrf_token(session_id: str) -> str:
    return _serializer().dumps(session_id)


def validate_csrf_token(session_id: str, token: str) -> bool:
    if not token or not session_id:
        return False
    try:
        value = _serializer().loads(token, max_age=_MAX_AGE)
        return value == session_id
    except BadData:
        return False
