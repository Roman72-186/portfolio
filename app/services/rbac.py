"""Seed default roles at app startup. Idempotent."""
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


# Модератор получил те же права, что «Главный преподаватель» (админ, rank 4) —
# решение владельца. Ранг в БД у модератора остаётся 3 (rank уникален,
# семь ролей не заводили), поэтому здесь — единственная точка, где «какая
# роль» превращается в «что можно». Вызывать при любом чтении rank роли,
# которое влияет на доступ (`get_current_user`, `user_management._role_rank`
# и т.п.), а не только там, где уже стоит `require_role`.
MODERATOR_EFFECTIVE_RANK = 4


def effective_role_rank(role_name: str | None, stored_rank: int) -> int:
    """Уровень доступа роли. Совпадает с rank в БД для всех ролей, кроме
    модератора — у него те же права, что у админа, при рангe 3 в базе."""
    if role_name == "модератор":
        return MODERATOR_EFFECTIVE_RANK
    return stored_rank


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
