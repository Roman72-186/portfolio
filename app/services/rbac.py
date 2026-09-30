"""Seed default roles at app startup. Idempotent."""
import re

from sqlalchemy.orm import Session as DBSession

from app.models.role import Role


ROLES = [
    (1, "ученик",     "Ученик"),
    (2, "куратор",    "Куратор"),
    (3, "модератор",  "Модератор"),
    # Внутреннее имя роли остаётся «админ» — по нему ходят ROLE_CABINET_MAP и
    # тесты. Владелец называет эту роль «Главный преподаватель», это и видит
    # человек. Существующим ролям seed display_name не обновляет, поэтому на
    # проде строку правит миграция c8e1a4f37b02.
    (4, "админ",      "Главный преподаватель"),
    (5, "суперадмин", "Суперадмин"),
]


MODERATOR_ROLE_NAME = "модератор"

# Модератор — наблюдатель (решение владельца 28.09.2026): видит «Учеников»
# с архивом и статистику, больше ничего и ничего не меняет. С 27.09 по
# 28.09.2026 у него были полные права «Главного преподавателя».
#
# Устроено в две части, и обе живут здесь — единственная точка, где «какая
# роль» превращается в «что можно»:
#   1. Уровень — как у ГП (4). Ранг в БД остаётся 3 (rank уникален), но все
#      проверки `require_role(4)`, «видит всю школу» и «кого можно назначить»
#      считают модератора ГП. Поэтому назначить модератора может только
#      суперадмин — иначе ГП раздавал бы роль через «3 < 4».
#   2. Белый список адресов поверх уровня (`is_moderator_request_allowed`,
#      вызывается из `get_current_user`). Всё, чего в списке нет, закрыто само:
#      новый роутер не нужно вспоминать и подшивать руками.
MODERATOR_EFFECTIVE_RANK = 4


def effective_role_rank(role_name: str | None, stored_rank: int) -> int:
    """Уровень доступа роли. Совпадает с rank в БД для всех ролей, кроме
    модератора — у него уровень ГП при ранге 3 в базе."""
    if role_name == MODERATOR_ROLE_NAME:
        return MODERATOR_EFFECTIVE_RANK
    return stored_rank


# Балл за любую работу ученика — пробник, отработка, промежуточный балл цикла,
# любая сдача в задании (домашка, контрольная, загрузка, «фото + сдача») — и
# закрытие цикла ставит только Главный преподаватель и выше (владелец
# 30.09.2026: «куратор не может оценивать работу ученика… только дать обратную
# связь… но он должен видеть оценку»). Отменяет решение 01.09.2026, когда балл
# открыли куратору. Одна точка правды: эндпоинты берут `require_scorer`
# (`app/dependencies.py`), шаблоны — флаг `can_score` отсюда. Сторож —
# `tests/test_score_rank_guard.py`.
SCORE_MIN_RANK = 4


def can_score(role_rank: int) -> bool:
    """Может ли сотрудник с этим уровнем (`effective_role_rank`) ставить балл."""
    return role_rank >= SCORE_MIN_RANK


# Разделы целиком: сама страница и всё под ней (`/cabinet/students/5/profile`,
# `/cabinet/students/5/legacy-portfolio`). Граница — по сегменту, чтобы
# `/cabinet/students-x` не проходил за `/cabinet/students`.
_MODERATOR_READ_SECTIONS = (
    "/cabinet/students",
    "/cabinet/archive",
    "/cabinet/superadmin/activity",
    "/cabinet/superadmin/stats",  # вместе с /stats/export
    "/static",
    "/auth",
)

# Отдельные адреса — точное совпадение. `/cabinet` только сам по себе:
# префиксом он открыл бы весь кабинет.
_MODERATOR_READ_EXACT = frozenset({
    "/",
    "/cabinet",
    "/cabinet/notifications/feed",  # опрос колокольчика из base.html
    "/cabinet/admin/registration-stats.csv",  # выгрузка со «Статистики активности»
    "/health",
    "/sw.js",
})

# Статистика цикла — единственная страница из «Программ», остальной раздел закрыт.
_MODERATOR_READ_PATTERNS = (
    re.compile(r"^/cabinet/staff/program/cycles/\d+/stats$"),
)

# Изменения, без которых кабинет не работает: выход и отметка своих же
# уведомлений прочитанными. Чужих данных они не трогают.
_MODERATOR_WRITE_EXACT = frozenset({
    "/logout",
    "/cabinet/notifications/mark-read",
    "/cabinet/superadmin/impersonate/stop",
})


def _in_section(path: str, section: str) -> bool:
    return path == section or path.startswith(section + "/")


def is_moderator_request_allowed(method: str, path: str) -> bool:
    """Открыт ли модератору этот запрос. Чтение — только из белого списка,
    изменение — только выход и свои уведомления."""
    if method in ("GET", "HEAD"):
        return (
            path in _MODERATOR_READ_EXACT
            or any(_in_section(path, s) for s in _MODERATOR_READ_SECTIONS)
            or any(p.match(path) for p in _MODERATOR_READ_PATTERNS)
        )
    return path in _MODERATOR_WRITE_EXACT


def seed_roles_and_permissions(db: DBSession) -> None:
    """Create roles if they don't exist. Safe to call on every startup."""
    existing_roles = {r.name: r for r in db.query(Role).all()}

    # Seed roles — по одной с SAVEPOINT, чтобы дубликат не ронял всю транзакцию
    for rank, name, display_name in ROLES:
        if name in existing_roles:
            continue
        try:
            with db.begin_nested():
                new_role = Role(rank=rank, name=name, display_name=display_name)
                db.add(new_role)
                db.flush()
            existing_roles[name] = new_role
        except Exception:
            # Роль уже есть в БД (race/ручная вставка) — перезачитываем
            existing_roles = {r.name: r for r in db.query(Role).all()}

    db.commit()
