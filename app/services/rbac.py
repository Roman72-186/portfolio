"""Seed default roles at app startup. Idempotent."""
from sqlalchemy.orm import Session as DBSession

from app.models.role import Role


ROLES = [
    (1, "ученик",     "Ученик"),
    (2, "куратор",    "Куратор"),
    (3, "модератор",  "Модератор"),
    (4, "админ",      "Админ"),
    (5, "суперадмин", "Суперадмин"),
]


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
