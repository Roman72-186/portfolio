"""Маскировка секретов в журнале запросов uvicorn.

Два входа несут секрет прямо в адресе: постоянная ссылка суперадмина
(`/auth/admin-access?key=…`, ключ вечный) и одноразовая ссылка входа
(`/auth/link?token=…`, живёт до первого входа). uvicorn пишет каждый запрос
в журнал `uvicorn.access` вместе со строкой запроса, так что утечка журнала
отдавала бы вход без пароля (код-ревью 28.09.2026, P2 № 15). Ссылку владелец
решил оставить — значит, прятать значение в журнале.

uvicorn кладёт путь со строкой запроса в `record.args[2]`
(`'%s - "%s %s HTTP/%s" %d'`), поэтому фильтр правит `args`, а не `msg`.
"""

from __future__ import annotations

import logging
import re

SECRET_QUERY_PARAMS = ("key", "token")

_SECRET_RE = re.compile(
    r"(?P<prefix>[?&](?:" + "|".join(SECRET_QUERY_PARAMS) + r")=)[^&#\s]*"
)


def mask_secret_query(path: str) -> str:
    return _SECRET_RE.sub(r"\g<prefix>***", path)


class SecretQueryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = args[:2] + (mask_secret_query(args[2]),) + args[3:]
        return True


def install() -> None:
    """Повесить фильтр на `uvicorn.access` один раз. Зовётся при импорте
    `app.main`: uvicorn настраивает журналы в каждом воркере до импорта
    приложения, так что фильтр переживает его `dictConfig`."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, SecretQueryFilter) for f in access.filters):
        access.addFilter(SecretQueryFilter())
