"""Свой VPN для учеников: выдача и отзыв ключей AmneziaWG.

План — `plans/2026-10-13-vpn-amnezia.md`. Ключ ученик получает у бота по
`/vpn` (`app/api/auth.py::_handle_vpn_request`), сами пиры создаёт служба
`deploy/vpn_keys/` на сервере в Нидерландах — платформа знает только её адрес
и токен, SSH-доступа к VPN-серверу у неё нет.

Здесь — единственное место правил:
- кому положен ключ — `is_eligible`;
- выдать или вернуть уже выданный — `get_or_issue`;
- отозвать — `revoke`;
- сверка раз в 15 минут — `reconcile` (`exam_scheduler._run_vpn_reconcile`).
  Отзыв при архиве, блокировке и удалении держится на ней, а не на хуках в
  `user_management`: право решает одна функция, и сверка ловит любое изменение
  состояния, в том числе сделанное скриптом или руками в базе.
"""
from __future__ import annotations

import base64
import logging
import ssl
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.config import settings
from app.models.user import User
from app.models.vpn_key import VpnKey

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Пир, которого ещё нет в базе, но он моложе этого срока, сверка не трогает:
# это может быть ключ, который прямо сейчас выдаётся и ещё не закоммичен.
ORPHAN_GRACE = timedelta(minutes=10)


class VpnKeysUnavailable(Exception):
    """Служба не настроена или не ответила — ученику «попробуй позже»."""


def enabled() -> bool:
    return bool(settings.vpn_keys_url and settings.vpn_keys_token)


def is_eligible(user: User) -> bool:
    """Ключ положен действующему участнику школы.

    Блокировка, архив и удаление ключ отнимают (три состояния — инвариант 8 в
    `AGENTS.md`). Истёкший `access_until` — нет: такой ученик должен войти и
    оплатить, а без VPN у части учеников сайт не открывается (инвариант 4)."""
    if not user.is_active or user.deleted_at is not None or user.archived_at is not None:
        return False
    return bool(user.role_id or user.is_admin)


def live_key(db: DBSession, user_id: int) -> VpnKey | None:
    return db.query(VpnKey).filter(VpnKey.user_id == user_id, VpnKey.revoked_at.is_(None)).first()


def label_for(user: User) -> str:
    """Подпись пира — её владелец видит в списке пользователей AmneziaVPN."""
    parts = [(user.name or "").strip() or "Ученик"]
    if user.tg_username:
        parts.append("@" + user.tg_username.lstrip("@"))
    parts.append(f"#{user.id}")
    return " ".join(parts)


# ── клиент службы ───────────────────────────────────────────────────────────

def _ssl_context() -> ssl.SSLContext | bool:
    """Доверяем только сертификату службы из настроек. Без него — системные
    центры сертификации (если служба когда-то встанет за нормальным доменом)."""
    if not settings.vpn_keys_cert_b64:
        return True
    pem = base64.b64decode(settings.vpn_keys_cert_b64).decode("ascii")
    return ssl.create_default_context(cadata=pem)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.vpn_keys_url.rstrip("/"),
        headers={"Authorization": f"Bearer {settings.vpn_keys_token}"},
        timeout=_TIMEOUT,
        verify=_ssl_context(),
    )


async def _call(method: str, path: str, **kwargs) -> dict:
    if not enabled():
        raise VpnKeysUnavailable("служба ключей не настроена")
    try:
        async with _client() as client:
            resp = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise VpnKeysUnavailable(f"служба ключей не ответила: {exc}") from exc
    if resp.status_code >= 400:
        raise VpnKeysUnavailable(f"служба ключей: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()


async def _server_create(label: str) -> tuple[str, str]:
    body = await _call("POST", "/peers", json={"label": label})
    return body["peer_id"], body["key"]


async def _server_delete(peer_id: str) -> None:
    await _call("DELETE", "/peers", params={"peer_id": peer_id})


async def _server_list() -> list[dict]:
    return (await _call("GET", "/peers")).get("peers") or []


# ── выдача и отзыв ──────────────────────────────────────────────────────────

async def get_or_issue(db: DBSession, user: User) -> str:
    """Ключ ученика: уже выданный или новый. Право проверяет вызывающий
    (`is_eligible`), чтобы ответить ученику своими словами."""
    existing = live_key(db, user.id)
    if existing:
        return existing.key

    peer_id, key = await _server_create(label_for(user))
    db.add(VpnKey(user_id=user.id, peer_id=peer_id, key=key))
    try:
        db.commit()
    except IntegrityError:
        # Два /vpn подряд: второй запрос успел выдать ключ раньше. Лишний пир
        # снимаем сразу, ученику — тот, что уже в базе.
        db.rollback()
        try:
            await _server_delete(peer_id)
        except VpnKeysUnavailable:
            logger.warning("vpn: лишний пир %s… не снят, снимет сверка", peer_id[:8])
        existing = live_key(db, user.id)
        if existing:
            return existing.key
        raise
    logger.info("vpn: ключ выдан user_id=%s peer=%s…", user.id, peer_id[:8])
    return key


async def revoke(db: DBSession, key: VpnKey) -> None:
    """Снять пир на сервере и пометить ключ отозванным. Сервер не ответил —
    ключ остаётся живым, отзыв добьёт сверка (`VpnKeysUnavailable` наружу)."""
    await _server_delete(key.peer_id)
    key.revoked_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("vpn: ключ отозван user_id=%s peer=%s…", key.user_id, key.peer_id[:8])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def reconcile(db: DBSession) -> dict:
    """Привести сервер и базу к одному списку. Три шага:

    1. Живые ключи тех, кто потерял право, — отозвать.
    2. Живые ключи, пира которых на сервере уже нет (владелец удалил ученика
       в своём приложении), — пометить отозванными.
    3. Пиры службы без живого ключа в базе (полное удаление ученика, сорванная
       запись) — снять, если они старше `ORPHAN_GRACE`.

    Пиры, которые владелец выпустил сам, служба не отдаёт — их сверка не видит."""
    stats = {"revoked": 0, "gone": 0, "orphans": 0, "errors": 0}
    if not enabled():
        return stats

    live = db.query(VpnKey).filter(VpnKey.revoked_at.is_(None)).all()
    users = {u.id: u for u in db.query(User).filter(User.id.in_([k.user_id for k in live])).all()} if live else {}
    for key in live:
        user = users.get(key.user_id)
        if user is not None and is_eligible(user):
            continue
        try:
            await revoke(db, key)
            stats["revoked"] += 1
        except VpnKeysUnavailable as exc:
            logger.warning("vpn reconcile: отзыв user_id=%s не прошёл: %s", key.user_id, exc)
            stats["errors"] += 1

    try:
        peers = await _server_list()
    except VpnKeysUnavailable as exc:
        logger.warning("vpn reconcile: список пиров недоступен: %s", exc)
        stats["errors"] += 1
        return stats

    on_server = {p["peer_id"]: p for p in peers if p.get("peer_id")}
    now = datetime.now(timezone.utc)
    live = db.query(VpnKey).filter(VpnKey.revoked_at.is_(None)).all()
    for key in live:
        if key.peer_id not in on_server:
            key.revoked_at = now
            stats["gone"] += 1
    if stats["gone"]:
        db.commit()

    live_ids = {k.peer_id for k in live if k.revoked_at is None}
    for peer_id, peer in on_server.items():
        if peer_id in live_ids:
            continue
        created = _parse_dt(peer.get("created_at"))
        if created is None or now - created < ORPHAN_GRACE:
            continue
        try:
            await _server_delete(peer_id)
            stats["orphans"] += 1
        except VpnKeysUnavailable as exc:
            logger.warning("vpn reconcile: сирота %s… не снята: %s", peer_id[:8], exc)
            stats["errors"] += 1

    if any(stats.values()):
        logger.info("vpn reconcile: %s", stats)
    return stats
