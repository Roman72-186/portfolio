"""Seed default roles and permissions at app startup. Idempotent."""
from sqlalchemy.orm import Session as DBSession

from app.models.role import Role, Permission, RolePermission


ROLES = [
    (1, "ученик",     "Ученик"),
    (2, "куратор",    "Куратор"),
    (3, "модератор",  "Модератор"),
    (4, "админ",      "Админ"),
    (5, "суперадмин", "Суперадмин"),
]

PERMISSIONS = [
    ("upload_photos",        "Загрузка фото"),
    ("view_own_gallery",     "Просмотр своей галереи"),
    ("view_upload_history",  "История загрузок"),
    ("take_exam",            "Прохождение экзамена"),
    ("view_own_students",    "Список своих учеников"),
    ("view_student_photos",  "Просмотр фото учеников"),
    ("comment_rate_work",    "Комментарии и оценки работ"),
    ("issue_magic_links",    "Выдача одноразовых ссылок"),
    ("view_all_students",    "Все ученики системы"),
    ("manage_curators",      "Управление кураторами"),
    ("ban_unban_users",      "Блокировка пользователей"),
    ("view_upload_stats",    "Статистика загрузок"),
    ("manage_tariffs",       "Управление тарифами"),
    ("assign_roles",         "Назначение ролей"),
    ("full_admin_panel",     "Полная административная панель"),
    ("manage_admins",        "Управление администраторами"),
    # Цикл Пробника + Feedback (план 2026-05-14)
    ("feedback.write",       "Запись обратной связи на работу"),
    ("feedback.view_all",    "Просмотр всех обратных связей"),
]

# Cumulative: each role includes all permissions of roles below it
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "ученик": [
        "upload_photos", "view_own_gallery", "view_upload_history", "take_exam",
    ],
    "куратор": [
        "upload_photos", "view_own_gallery", "view_upload_history", "take_exam",
        "view_own_students", "view_student_photos", "comment_rate_work", "issue_magic_links",
        "feedback.write",
    ],
    "модератор": [
        "upload_photos", "view_own_gallery", "view_upload_history", "take_exam",
    ],
    "админ": [
        "upload_photos", "view_own_gallery", "view_upload_history", "take_exam",
        "view_own_students", "view_student_photos", "comment_rate_work", "issue_magic_links",
        "view_all_students", "manage_curators", "ban_unban_users", "view_upload_stats",
        "manage_tariffs", "assign_roles", "full_admin_panel",
        "feedback.write", "feedback.view_all",
    ],
    "суперадмин": [
        "upload_photos", "view_own_gallery", "view_upload_history", "take_exam",
        "view_own_students", "view_student_photos", "comment_rate_work", "issue_magic_links",
        "view_all_students", "manage_curators", "ban_unban_users", "view_upload_stats",
        "manage_tariffs", "assign_roles", "full_admin_panel",
        "manage_admins",
        "feedback.write", "feedback.view_all",
    ],
}


def seed_roles_and_permissions(db: DBSession) -> None:
    """Create roles and permissions if they don't exist. Safe to call on every startup.

    Оптимизация: загружаем все роли/permissions одним запросом, затем batch insert.
    """
    # Batch load existing roles and permissions
    existing_roles = {r.name: r for r in db.query(Role).all()}
    existing_perms = {p.codename: p for p in db.query(Permission).all()}

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

    # Seed permissions — аналогично
    for codename, description in PERMISSIONS:
        if codename in existing_perms:
            continue
        try:
            with db.begin_nested():
                new_perm = Permission(codename=codename, description=description)
                db.add(new_perm)
                db.flush()
            existing_perms[codename] = new_perm
        except Exception:
            existing_perms = {p.codename: p for p in db.query(Permission).all()}

    # Batch load existing role_permissions
    all_role_perms = db.query(RolePermission).all()
    existing_pairs = {(rp.role_id, rp.permission_id) for rp in all_role_perms}

    # Seed role_permissions
    for role_name, perm_codenames in ROLE_PERMISSIONS.items():
        role = existing_roles.get(role_name)
        if not role:
            continue
        for codename in perm_codenames:
            perm = existing_perms.get(codename)
            if perm and (role.id, perm.id) not in existing_pairs:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))
                existing_pairs.add((role.id, perm.id))

    db.commit()
