"""Откуда входят ученики — `Session.ip` и `Session.user_agent`, с 14.10.2026.

Школа хочет видеть, не делят ли каникулярный доступ на несколько человек
(созвон 13.10.2026). Адрес пишет `auth._create_session_response` при каждом
настоящем входе. Входы «глазами» сюда не попадают: их сессия заводится
сотрудником, и адрес у неё — его.

Сессии до 14.10.2026 адреса не несут — они не считаются, а не показываются
пустыми.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app.models.session import Session
from app.services.tz import msk_text

# Окно «за неделю» — неделя группы каникулярного набора.
IP_WINDOW = timedelta(days=7)
RECENT_LOGINS_LIMIT = 10


def _real_logins(db: DBSession):
    return db.query(Session).filter(
        Session.impersonated_by_id.is_(None),
        Session.ip.isnot(None),
    )


def distinct_ips_by_user(db: DBSession, user_ids: list[int]) -> dict[int, int]:
    """Сколько разных адресов у каждого ученика за последнюю неделю."""
    if not user_ids:
        return {}
    since = datetime.now(timezone.utc) - IP_WINDOW
    rows = (
        _real_logins(db)
        .with_entities(Session.user_id, func.count(func.distinct(Session.ip)))
        .filter(Session.user_id.in_(user_ids), Session.created_at >= since)
        .group_by(Session.user_id)
        .all()
    )
    return {user_id: count for user_id, count in rows}


def recent_logins(db: DBSession, user_id: int) -> list[Session]:
    """Последние входы ученика — для карточки: когда, с какого адреса и браузера."""
    return (
        _real_logins(db)
        .filter(Session.user_id == user_id)
        .order_by(Session.created_at.desc())
        .limit(RECENT_LOGINS_LIMIT)
        .all()
    )


# Устройство по строке браузера — грубо, но читаемо: школе важно отличить
# «телефон и ноутбук одного ученика» от «пяти разных телефонов».
_DEVICES = (
    ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
    ("Windows", "Windows"), ("Macintosh", "Mac"), ("Linux", "Linux"),
)


def device_label(user_agent: str | None) -> str:
    for marker, label in _DEVICES:
        if user_agent and marker in user_agent:
            return label
    return "Неизвестное устройство"


def recent_logins_json(db: DBSession, user_id: int) -> list[dict]:
    return [
        {"at": msk_text(s.created_at), "ip": s.ip, "device": device_label(s.user_agent)}
        for s in recent_logins(db, user_id)
    ]
