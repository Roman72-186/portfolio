"""Контактные данные ученика: нормализация и проверка одного набора полей.

Вынесено из `app/api/cabinet_student.py`, потому что редакторов у этих полей
стало два и правила должны совпадать в обоих:

- анкета первого входа (`POST /cabinet/profile`) — заполняется один раз,
  вместе с установочными данными (ФИО, тариф, даты);
- «Изменить контакты» в личной информации (`POST /cabinet/personal/contacts`) —
  открыт ученику всегда, но только на эти три поля.

Установочные данные после первого входа ученик не меняет — их правит куратор
через `POST /cabinet/students/{student_id}/profile`.
"""
import re

from sqlalchemy.orm import Session as DBSession

from app.models.role import Role
from app.models.user import User

PHONE_RE = re.compile(r'^[\d\s\+\-\(\)]{7,20}$')
TG_RE = re.compile(r'^[A-Za-z0-9_]{4,32}$')


def normalize_phone(raw: str) -> str:
    return (raw or "").strip()


def normalize_tg_username(raw: str) -> str:
    """Ник без ведущей собаки: в БД хранится голым, «@» дорисовывает шаблон."""
    return (raw or "").strip().lstrip("@")


def validate_contacts(phone: str, parent_phone: str, tg_username: str) -> list[str]:
    """Список ошибок для показа ученику. Пустой список — можно сохранять.

    Значения ожидаются уже нормализованными.
    """
    errors: list[str] = []

    if not phone:
        errors.append("Введите номер телефона")
    elif not PHONE_RE.match(phone):
        errors.append("Введите корректный номер телефона (только цифры, пробелы, +, -, скобки)")

    if not parent_phone:
        errors.append("Введите номер телефона родителя")
    elif not PHONE_RE.match(parent_phone):
        errors.append("Введите корректный номер телефона родителя")

    if not tg_username:
        errors.append("Укажите ник в Telegram")
    elif not TG_RE.match(tg_username):
        errors.append("Ник Telegram: только латиница, цифры, _ (4–32 символа)")

    return errors


def find_student_by_tg_username(
    db: DBSession, tg_username: str, *, exclude_user_id: int | None = None
) -> User | None:
    """Ученик с таким ником в Telegram, кроме `exclude_user_id`.

    Ник шифруется при хранении (`EncryptedString`), поэтому сравнить его
    условием в SQL нельзя: кандидатов приходится расшифровывать и сравнивать в
    Python. Отсюда же и `limit` — выборка ограничена, полного сканирования
    таблицы на каждое сохранение контактов не происходит.

    Зачем проверять занятость: по нику суперадмин заводит учеников пачкой
    (`cabinet_superadmin.py`), а n8n ищет по нему папку с работами в Google
    Drive (`services/drive.py`). Два ученика с одним ником сводят разных людей
    в одну карточку.
    """
    username = normalize_tg_username(tg_username).lower()
    if not username:
        return None

    candidates = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == 1, User.deleted_at.is_(None), User.archived_at.is_(None))
        .order_by(User.profile_completed.desc(), User.updated_at.desc(), User.id.desc())
        .limit(5000)
        .all()
    )
    for candidate in candidates:
        if exclude_user_id is not None and candidate.id == exclude_user_id:
            continue
        if normalize_tg_username(candidate.tg_username or "").lower() == username:
            return candidate
    return None
