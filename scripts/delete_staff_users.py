"""
delete_staff_users.py — массовое удаление персонала: кураторов и Главных
преподавателей, кроме перечисленных в `--keep`.

Удаление мягкое, ровно как кнопка «Удалить» в карточке пользователя: вызывает
`app/services/user_management.py::soft_delete_user`, то есть ставит
`deleted_at`, гасит `is_active`, выкидывает человека из активных сессий и
пишет строку в аудит-лог. Работы, оценки и переписки остаются в базе.

Трогает только роли «куратор» (rank=2) и «админ» / Главный преподаватель
(rank=4). Модератор (rank=3), ученики и суперадмин не затрагиваются. Уже
удалённые пропускаются — скрипт можно запускать повторно.

Ученики, привязанные к удалённому куратору, сохраняют `curator_id`: имя в
карточке ученика остаётся, но человек пропадает из рабочих списков и войти
больше не может. Переназначить кураторов — «Массовое назначение куратора» в
кабинете суперадмина.

Запуск на проде (внутри контейнера, DATABASE_URL уже в окружении):

    docker exec portfolio-saas-app-1 python scripts/delete_staff_users.py --list

    docker exec portfolio-saas-app-1 python scripts/delete_staff_users.py --keep 10

    docker exec portfolio-saas-app-1 python scripts/delete_staff_users.py --keep 10 --apply

Без `--apply` скрипт ничего не пишет: показывает, кого бы удалил.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import SessionLocal  # noqa: E402
from app.models.role import Role  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.user_management import soft_delete_user  # noqa: E402

CURATOR_RANK = 2
HEAD_TEACHER_RANK = 4
STAFF_RANKS = (CURATOR_RANK, HEAD_TEACHER_RANK)
SUPERADMIN_RANK = 5


def _display(u: User) -> str:
    name = f"{u.last_name or ''} {u.first_name or u.name}".strip()
    role = u.role.display_name if u.role else "—"
    state = "удалён" if u.deleted_at else ("активен" if u.is_active else "заблокирован")
    login = u.staff_login or "—"
    return f"id={u.id:<6} {name:<26} {role:<24} логин={login:<14} {state}"


def _staff(db, *, include_deleted: bool = False):
    q = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank.in_(STAFF_RANKS))
    )
    if not include_deleted:
        q = q.filter(User.deleted_at.is_(None))
    return q.order_by(Role.rank, User.id).all()


def _students_count(db, curator_id: int) -> int:
    return (
        db.query(User)
        .filter(User.curator_id == curator_id, User.deleted_at.is_(None))
        .count()
    )


def _resolve_actor(db, performed_by: int | None) -> User | None:
    if performed_by:
        actor = db.query(User).filter(User.id == performed_by).first()
        if not actor:
            print(f"Пользователь id={performed_by} не найден")
            return None
        rank = actor.role.rank if actor.role else 0
        if rank < SUPERADMIN_RANK:
            print(f"id={performed_by} — не суперадмин (ранг {rank})")
            return None
        return actor

    candidates = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == SUPERADMIN_RANK, User.deleted_at.is_(None))
        .all()
    )
    if len(candidates) == 1:
        return candidates[0]
    print("Суперадминов несколько или нет — укажите --performed-by <id>:")
    for c in candidates:
        print("   ", _display(c))
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Удалить кураторов и Главных преподавателей, кроме указанных"
    )
    parser.add_argument("--list", action="store_true",
                        help="показать весь персонал этих ролей и выйти")
    parser.add_argument("--keep", type=int, nargs="*", default=None,
                        help="id тех, кого оставить с доступом")
    parser.add_argument("--keep-none", action="store_true",
                        help="удалить вообще всех кураторов и ГП")
    parser.add_argument("--performed-by", type=int, default=None,
                        help="id суперадмина, от чьего имени пишется аудит-лог")
    parser.add_argument("--apply", action="store_true",
                        help="выполнить (без флага — только показать)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            rows = _staff(db, include_deleted=True)
            print(f"Кураторов и Главных преподавателей всего: {len(rows)}")
            for u in rows:
                print("  ", _display(u))
            return

        keep_ids = set(args.keep or [])
        if not keep_ids and not args.keep_none:
            print("Не указано, кого оставить. Задайте --keep <id> [<id> …] или --keep-none.")
            sys.exit(1)

        # Опечатка в id означала бы, что нужного человека тоже удалят.
        all_staff = {u.id: u for u in _staff(db, include_deleted=True)}
        unknown = sorted(keep_ids - set(all_staff))
        if unknown:
            print(f"Эти id не найдены среди кураторов и ГП: {unknown}")
            sys.exit(1)

        for uid in sorted(keep_ids):
            print(f"Остаётся с доступом: {_display(all_staff[uid])}")

        targets = [u for u in _staff(db) if u.id not in keep_ids]
        print(f"\nБудут удалены: {len(targets)}")
        for u in targets:
            students = _students_count(db, u.id)
            tail = f"  учеников на нём: {students}" if students else ""
            print("  ", _display(u) + tail)

        if not args.apply:
            print("\nЭто прогон без записи. Повторите с --apply, чтобы выполнить.")
            return

        actor = _resolve_actor(db, args.performed_by)
        if actor is None:
            sys.exit(1)

        done, failed = 0, []
        for u in targets:
            if soft_delete_user(db, target_user_id=u.id, performed_by_id=actor.id):
                done += 1
            else:
                failed.append(u.id)

        print(f"\nУдалено: {done}")
        if failed:
            print(f"Не удалось (проверьте роль и права): {failed}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
