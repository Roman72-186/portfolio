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

# Международные номера храним в компактном формате с кодом страны. Ограничение
# в 15 цифр соответствует максимальной длине международного номера; нижняя
# граница отсекает явно неполный ввод, не пытаясь угадывать план нумерации
# каждой страны.
PHONE_RE = re.compile(r'^\+[1-9]\d{7,14}$')
PHONE_CHARS_RE = re.compile(r'^[\d\s+().-]+$')
TG_RE = re.compile(r'^[A-Za-z0-9_]{4,32}$')


def normalize_phone(raw: str) -> str:
    """Телефон к международному виду `+<код страны><номер>`.

    Человек вводит номер как привык: «8 999 123-45-67», «+7 (999) 123 45 67»,
    «79991234567», «9991234567». Все эти записи — один номер, и в базе он
    должен лежать одной строкой, иначе поиск по номеру и выгрузка контактов
    считают их разными людьми.

    Российский ввод без международного префикса по-прежнему распознаём по
    привычным 10 цифрам или ведущей «8». Для остальных стран нужен «+» или
    международный префикс «00». Пробелы, скобки, точки и дефисы убираются.

    Чего разобрать не вышло, возвращаем как есть: решение о показе ошибки
    принимает `validate_contacts`, иначе ученик увидел бы пустое поле вместо
    своего ввода.
    """
    value = (raw or "").strip()
    if not value:
        return ""

    if not PHONE_CHARS_RE.fullmatch(value):
        return value

    digits = re.sub(r"\D", "", value)
    if value.startswith("00") and len(digits) >= 3:
        return "+" + digits[2:]
    if value.startswith("+") and value.count("+") == 1:
        return "+" + digits
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    return value


def normalize_tg_username(raw: str) -> str:
    """Ник без ведущей собаки: в БД хранится голым, «@» дорисовывает шаблон."""
    return (raw or "").strip().lstrip("@")


def validate_contacts(phone: str, parent_phone: str, tg_username: str) -> list[str]:
    """Список ошибок для показа ученику. Пустой список — можно сохранять.

    Значения ожидаются уже нормализованными.
    """
    errors: list[str] = []

    if not phone:
        errors.append("Введи номер телефона")
    elif not PHONE_RE.match(phone):
        errors.append("Укажи код страны и номер, например +7 999 123-45-67")

    if not parent_phone:
        errors.append("Введи номер телефона родителя")
    elif not PHONE_RE.match(parent_phone):
        errors.append("Укажи телефон родителя с кодом страны, например +7 999 123-45-67")

    if not tg_username:
        errors.append("Укажи ник в Telegram")
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
