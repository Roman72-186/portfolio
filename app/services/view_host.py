"""Адрес просмотра «глазами» — отдельный хост для чужого кабинета.

Владелец 08.10.2026: ГП и суперадмин входят в кабинет ученика или куратора и не
теряют свой. Кука `session_id` ставится без `Domain`, поэтому на поддомене у
браузера свой пропуск: просмотр живёт на `settings.view_origin`, свой кабинет на
основном адресе остаётся нетронутым во всех вкладках.

Пустой `view_origin` — режим выключен, вход «глазами» подменяет куку на
основном адресе (прежнее поведение). План — `plans/2026-10-08-apparchi-просмотр-глазами-и-черновики.md`.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from app.config import settings


def enabled() -> bool:
    return bool(settings.view_origin)


def _host(origin: str) -> str:
    return (urlsplit(origin).hostname or "").lower()


def is_view_request(request: Request) -> bool:
    """Запрос пришёл на адрес просмотра. Хост — из заголовка `Host`, его
    Traefik передаёт как есть; порт не сравниваем (стенд на localhost)."""
    if not enabled():
        return False
    return (request.url.hostname or "").lower() == _host(settings.view_origin)


def view_url(path: str) -> str:
    return settings.view_origin.rstrip("/") + path


def main_url(path: str) -> str:
    origin = settings.main_origin or f"https://{settings.domain}"
    return origin.rstrip("/") + path
