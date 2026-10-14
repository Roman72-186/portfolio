"""Свой VPN для учеников: выдача и отзыв ключей AmneziaWG.

План — `plans/2026-10-13-vpn-amnezia.md`. Ключ запрашивают на экране входа
(владелец 14.10.2026: «запрос делать не в боте, а при входе»): кто не может
войти, вводит свой ник под кнопкой «Войти через Telegram», и бот присылает ему
в Telegram ключ с инструкцией (`POST /auth/vpn-request` → `send_key_by_username`).
Ключ получает только знакомый ученик — с привязанным Telegram; новичку ключ
выдаёт поддержка. Сами пиры создаёт служба `deploy/vpn_keys/` на сервере в
Нидерландах — платформа знает только её адрес и токен, SSH-доступа нет.

Здесь — единственное место правил:
- кому положен ключ — `is_eligible`;
- выдать или вернуть уже выданный — `get_or_issue`;
- найти ученика по нику и прислать ключ — `send_key_by_username`;
- отозвать — `revoke`;
- сверка раз в 15 минут — `reconcile` (`exam_scheduler._run_vpn_reconcile`).
  Отзыв при архиве, блокировке и удалении держится на ней, а не на хуках в
  `user_management`: право решает одна функция, и сверка ловит любое изменение
  состояния, в том числе сделанное скриптом или руками в базе.
"""
from __future__ import annotations

import base64
import html
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


# ── запрос с экрана входа ───────────────────────────────────────────────────

# Прямые ссылки на приложения (сверены 14.10.2026). AmneziaVPN спрятана в
# российском App Store — на iPhone ставится DefaultVPN тех же разработчиков.
# amnezia.org в России открывается не у всех, поэтому у компьютера запасная
# ссылка на выпуски в GitHub.
ANDROID_URL = "https://play.google.com/store/apps/details?id=org.amnezia.vpn"
IOS_URL = "https://apps.apple.com/ru/app/defaultvpn/id6744725017"
DESKTOP_URL = "https://amnezia.org/ru/downloads"
DESKTOP_MIRROR_URL = "https://github.com/amnezia-vpn/amnezia-client/releases/latest"

# Одна отправка ключа на ученика за это время: чужой ник в форме не должен
# превращаться в поток сообщений хозяину ника.
SEND_COOLDOWN_SECONDS = 600


def key_message(key: str) -> str:
    """Сообщение бота с ключом и инструкцией (HTML-разметка Telegram)."""
    return (
        "Ключ VPN для входа на сайт школы – нажми на него, и он скопируется:\n\n"
        f"<code>{html.escape(key)}</code>\n\n"
        "<b>1. Установи приложение</b>\n"
        f"• Android – AmneziaVPN из <a href=\"{ANDROID_URL}\">Google Play</a>\n"
        f"• iPhone и iPad – DefaultVPN из <a href=\"{IOS_URL}\">App Store</a> (нужна iOS 16 или новее)\n"
        f"• Компьютер – AmneziaVPN с <a href=\"{DESKTOP_URL}\">amnezia.org</a>; "
        f"если сайт не открывается – <a href=\"{DESKTOP_MIRROR_URL}\">с GitHub</a>\n\n"
        "<b>2. Добавь ключ</b>\n"
        "В приложении нажми «+», потом «Вставить» и «Подключиться».\n\n"
        f"<b>3. Войди на сайт</b>\n"
        f"Открой {settings.domain} и нажми «Войти через Telegram». После входа VPN "
        "можно выключить, пока сайт открывается без него.\n\n"
        "Ключ личный. Если им начнёт пользоваться кто-то ещё, его отключат."
    )


UNAVAILABLE_MESSAGE = (
    "Сейчас не получается выдать ключ VPN. Попробуй запросить его на сайте "
    "через несколько минут, а если не выйдет – напиши в поддержку."
)


def find_linked_user_by_username(db: DBSession, raw_username: str) -> User | None:
    """Ученик или сотрудник с этим ником и привязанным Telegram.

    Ник хранится зашифрованным (`EncryptedString`) — условием SQL его не
    сравнить, кандидатов расшифровываем в Python, как
    `contacts.find_student_by_tg_username`. Кандидаты — только с привязанным
    Telegram: ключ уходит в чат, без чата его некуда прислать."""
    from app.services.contacts import normalize_tg_username

    username = normalize_tg_username(raw_username).lower()
    if not username:
        return None
    candidates = (
        db.query(User)
        .filter(User.telegram_chat_id.isnot(None), User.deleted_at.is_(None))
        .order_by(User.id.desc())
        .limit(5000)
        .all()
    )
    for candidate in candidates:
        if normalize_tg_username(candidate.tg_username or "").lower() == username:
            return candidate
    return None


async def send_key_by_username(raw_username: str) -> None:
    """Запрос с экрана входа: прислать ключ в Telegram хозяину ника.

    Зовётся фоновой задачей после ответа странице: ответ одинаковый и
    одинаково быстрый для любого ника, поэтому по форме не узнать, учится ли у
    нас человек. Незнакомый ник, нет права, пауза ещё не прошла — ничего не
    шлём, только строка в журнале. Сессию базы открывает сама: сессия запроса к
    этому моменту уже закрыта."""
    from app.cache import claim_vpn_key_send
    from app.db.database import SessionLocal
    from app.services import telegram as telegram_service

    db = SessionLocal()
    try:
        user = find_linked_user_by_username(db, raw_username)
        if user is None or not is_eligible(user):
            logger.info("vpn: запрос с экрана входа — ник не найден или нет права")
            return
        if not claim_vpn_key_send(user.id, SEND_COOLDOWN_SECONDS):
            logger.info("vpn: user_id=%s — ключ уже отправлялся недавно", user.id)
            return
        chat_id = user.telegram_chat_id
        try:
            key = await get_or_issue(db, user)
        except VpnKeysUnavailable as exc:
            logger.warning("vpn: ключ для user_id=%s не выдан: %s", user.id, exc)
            await telegram_service.send_message(chat_id, UNAVAILABLE_MESSAGE)
            return
        sent = await telegram_service.send_message(chat_id, key_message(key))
        logger.info("vpn: ключ user_id=%s отправлен в Telegram: %s", user.id, sent)
    except Exception:
        logger.exception("vpn: ошибка при отправке ключа по запросу с экрана входа")
        db.rollback()
    finally:
        db.close()


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
