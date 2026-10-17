"""Ссылки набора (`app/models/intake_link.py`): пробный набор предобучения
(`/proba`, годовая программа) и группы каникулярного набора (`/join/<slug>`,
с 14.10.2026).

Одна модель, одна строка на slug — кэш не нужен, запрос идёт по уникальному
индексу. Здесь все правила ссылок: что получает пришедший по ней новичок и
кого вход пускает без проверки Telegram-канала. Роуты (`api/auth.py`,
`api/cabinet_superadmin.py`) своих копий условий не держат.
"""
import secrets
from datetime import datetime

from sqlalchemy.orm import Session as DBSession

from app.constants import INTAKE_TRIAL_SLUG, PROGRAM_HOLIDAY
from app.models.intake_link import IntakeLink
from app.models.user import User
from app.services.tags import add_tag_to_user

# Алфавит адреса группы: без похожих друг на друга знаков (0/o, 1/l), чтобы
# ссылку можно было продиктовать или переписать с экрана.
_SLUG_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
_SLUG_LENGTH = 8


def get_link(db: DBSession, slug: str) -> IntakeLink | None:
    return db.query(IntakeLink).filter(IntakeLink.slug == slug).first()


def is_open(link: IntakeLink | None) -> bool:
    """Ссылка работает как вход прямо сейчас: включена и дата задана."""
    return bool(link and link.is_active and link.access_until is not None)


def is_holiday(link: IntakeLink | None) -> bool:
    return bool(link and link.program == PROGRAM_HOLIDAY)


def channel_check_skipped(user: User | None, link: IntakeLink | None) -> bool:
    """Пускать ли вход без проверки закрытого Telegram-канала.

    Каникулярный набор — внешние ученики, в канале школы их нет (созвон
    13.10.2026). Уже заведённого человека судит его собственная программа, а не
    ссылка: годовой ученик по каникулярной ссылке проходит обычную проверку и
    ничего не меняет (правило `/proba` — ссылка действует только на новичка),
    каникулярный входит без канала и по ссылке, и с главной, и через бота.
    Новичка — ссылка: только каникулярная заводит его без канала.
    """
    if user is not None:
        return user.program == PROGRAM_HOLIDAY
    return is_holiday(link)


def apply_to_new_user(db: DBSession, user: User, link: IntakeLink | None) -> None:
    """Что получает только что заведённый по ссылке человек. Уже существующих
    не трогает вызывающий код: срок закрыл бы доступ оплатившему ученику.

    Срок берётся независимо от `is_active`: между кликом по ссылке и
    возвратом из Telegram проходит до 10 минут, и выключи владелец `/proba` в
    это окно — бессрочный доступ хуже срока. Снимок на момент входа: правка
    даты потом вошедших не сдвигает. Тариф снимается вместе со сроком:
    аккаунт заводится с «УВЕРЕННЫЙ» по умолчанию, и пробнику молча открылись
    бы материалы старшего тарифа (владелец 14.09.2026). «Без тарифа» в базе —
    пустая строка, колонка NOT NULL. Без срока тариф не снимаем — такого
    человека не видно фильтром «Со сроком доступа».

    Каникулярному программа и тег группы ставятся всегда: в канал его не
    проверяли, и годовым он стать не может — увидел бы годовой курс.
    Закрытая ссылка группы новичка сюда не пускает вовсе (callback).
    """
    if link is None:
        return
    if link.access_until is not None:
        user.access_until = link.access_until
        user.tariff = ""
    if is_holiday(link):
        user.program = PROGRAM_HOLIDAY
        user.tariff = ""
        # Платный трек тег при входе не даёт — его ставит оплата
        # (`payments.record_paid`), иначе вход по адресу трека обходил бы её.
        if link.tag_id is not None and not link.price_kop:
            add_tag_to_user(db, user.id, link.tag_id)


def holiday_links(db: DBSession) -> list[IntakeLink]:
    return (
        db.query(IntakeLink)
        .filter(IntakeLink.program == PROGRAM_HOLIDAY)
        .order_by(IntakeLink.created_at, IntakeLink.id)
        .all()
    )


# ---------------------------------------------------------------------------
# Треки каникулярного набора (17.10.2026, план
# `plans/2026-10-17-apparchi-оплата-трека.md`)
#
# Трек — каникулярная ссылка группы с тегом. Трек ученика — его тег: оплата
# ставит тег, возврат снимает, сотрудник может поставить его руками (оплатил
# мимо платформы). Общая ссылка набора — каникулярная ссылка без тега: она
# заводит человека без трека, и он видит только страницу оплаты.
# ---------------------------------------------------------------------------

def is_track(link: IntakeLink | None) -> bool:
    return is_holiday(link) and link.tag_id is not None


def holiday_tracks(db: DBSession) -> list[IntakeLink]:
    return [link for link in holiday_links(db) if link.tag_id is not None]


def tracks_on_sale(db: DBSession) -> list[IntakeLink]:
    """Что показать на странице оплаты: трек с ценой, включён и с датой
    доступа — без даты оплата не дала бы срока."""
    return [
        link for link in holiday_tracks(db)
        if link.price_kop and link.is_active and link.access_until is not None
    ]


def entry_link(db: DBSession) -> IntakeLink | None:
    """Общая ссылка набора — каникулярная без тега."""
    return (
        db.query(IntakeLink)
        .filter(IntakeLink.program == PROGRAM_HOLIDAY, IntakeLink.tag_id.is_(None))
        .order_by(IntakeLink.id)
        .first()
    )


def track_of(db: DBSession, user_id: int) -> IntakeLink | None:
    """Трек ученика — каникулярная ссылка, чей тег у него стоит. Тегов трека
    у одного человека не бывает больше одного, пока нет доплаты до большого
    трека; если бывает — первый по порядку заведения."""
    from app.models.tag import UserTag

    return (
        db.query(IntakeLink)
        .join(UserTag, UserTag.tag_id == IntakeLink.tag_id)
        .filter(UserTag.user_id == user_id, IntakeLink.program == PROGRAM_HOLIDAY)
        .order_by(IntakeLink.created_at, IntakeLink.id)
        .first()
    )


def tracks_by_user(db: DBSession, user_ids: list[int]) -> dict[int, IntakeLink]:
    """`track_of` для многих — одним запросом (напоминания по расписанию)."""
    from app.models.tag import UserTag

    if not user_ids:
        return {}
    rows = (
        db.query(UserTag.user_id, IntakeLink)
        .join(IntakeLink, IntakeLink.tag_id == UserTag.tag_id)
        .filter(UserTag.user_id.in_(user_ids), IntakeLink.program == PROGRAM_HOLIDAY)
        .order_by(IntakeLink.created_at, IntakeLink.id)
        .all()
    )
    result: dict[int, IntakeLink] = {}
    for user_id, link in rows:
        result.setdefault(user_id, link)
    return result


def needs_track(db: DBSession, user: User) -> bool:
    """Каникулярный без трека — до оплаты ему открыта только страница треков
    (`dependencies.get_current_user`)."""
    return user.program == PROGRAM_HOLIDAY and track_of(db, user.id) is None


def create_entry_link(
    db: DBSession, *, access_until: datetime | None, created_by_id: int,
) -> IntakeLink:
    """Общая ссылка набора — выключенной, как ссылка группы."""
    link = IntakeLink(
        slug=_new_slug(db),
        title="Каникулярный набор",
        program=PROGRAM_HOLIDAY,
        tag_id=None,
        access_until=access_until,
        is_active=False,
        created_by_id=created_by_id,
    )
    db.add(link)
    db.flush()
    return link


def _new_slug(db: DBSession) -> str:
    while True:
        slug = "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LENGTH))
        if slug != INTAKE_TRIAL_SLUG and get_link(db, slug) is None:
            return slug


def create_holiday_link(
    db: DBSession, *, title: str, tag_id: int, access_until: datetime | None,
    created_by_id: int,
) -> IntakeLink:
    """Новая ссылка группы — выключенной: включает её человек, проверив дату."""
    link = IntakeLink(
        slug=_new_slug(db),
        title=title,
        program=PROGRAM_HOLIDAY,
        tag_id=tag_id,
        access_until=access_until,
        is_active=False,
        created_by_id=created_by_id,
    )
    db.add(link)
    db.flush()
    return link
