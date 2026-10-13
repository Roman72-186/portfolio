"""Shared SlowAPI rate limiter instance.

Счётчики живут в Redis, а не в памяти процесса (код-ревью 12.10.2026, P2 № 6):
на проде четыре воркера (`--workers 4`), и у каждого был свой счётчик — `10/minute`
на `POST /login` на деле пропускал до 40 попыток, а перезапуск воркера каждые
5000 запросов обнулял его. Адрес — тот же `settings.redis_url`, что у `app/cache.py`.

Упал Redis — вход не ломается: `in_memory_fallback_enabled` переводит лимитер
на счётчик в памяти процесса (прежнее поведение), а SlowAPI сам время от времени
проверяет, ожил ли Redis. Таймауты как у `app/cache.py`: без них зависший Redis
подвесил бы каждый запрос на вход.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

_REDIS_OPTIONS = {"socket_connect_timeout": 1, "socket_timeout": 1}


def build_limiter(storage_uri: str) -> Limiter:
    options = _REDIS_OPTIONS if storage_uri.startswith(("redis://", "rediss://")) else {}
    return Limiter(
        key_func=get_remote_address,
        storage_uri=storage_uri,
        storage_options=dict(options),
        in_memory_fallback_enabled=True,
    )


limiter = build_limiter(settings.redis_url)
