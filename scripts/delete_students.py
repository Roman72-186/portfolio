"""
delete_students.py — массовое удаление учеников (рангом 1), по образцу
`delete_staff_users.py`.

Удаление мягкое, ровно как кнопка «Удалить» в карточке пользователя: вызывает
`app/services/user_management.py::soft_delete_user`, то есть ставит
`deleted_at`, гасит `is_active`, выкидывает человека из активных сессий и
пишет строку в аудит-лог. Работы, оценки, циклы, переписки и файлы в S3
остаются в базе — это не хард-делит.

Трогает только роль «ученик» (rank=1). Куратор, модератор, админ/Главный
преподаватель, суперадмин не затрагиваются. Уже удалённые пропускаются —
скрипт можно запускать повторно.

Важно (инвариант проекта, AGENTS.md п.8): `deleted_at` снимается при любом
входе — `auth._upsert_user`/`_upsert_telegram_user` обнуляют его и включают
`is_active`. Значит soft-delete не мешает тестовому ученику зайти снова тем
же Telegram-аккаунтом: он просто вернётся активным с прежним профилем
(`profile_completed` не сбрасывается). Для «чистой» повторной анкеты нужен
новый пользователь, не воскрешение старого.

Запуск на проде (внутри контейнера, DATABASE_URL уже в окружении):

    docker exec portfolio-saas-app-1 python scripts/delete_students.py --list

    docker exec portfolio-saas-app-1 python scripts/delete_students.py --keep-none --apply

    docker exec portfolio-saas-app-1 python scripts/delete_students.py --keep 155 --apply

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

STUDENT_RANK = 1
SUPERADMIN_RANK = 5


def _display(u: User) -> str:
    name = f"{u.last_name or ''} {u.first_name or u.name}".strip()
    curator = u.curator_id or "—"
    state = "удалён" if u.deleted_at else ("активен" if u.is_active else "заблокирован")
    tg = u.tg_username or "—"
    return f"id={u.id:<6} {name:<26} tg={tg:<20} куратор={curator:<8} {state}"


def _students(db, *, include_deleted: bool = False):
    q = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == STUDENT_RANK)
    )
    if not include_deleted:
        q = q.filter(User.deleted_at.is_(None))
    return q.order_by(User.id).all()


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
        description="Удалить учеников (rank=1), кроме указанных"
    )
    parser.add_argument("--list", action="store_true",
                        help="показать всех учеников и выйти")
    parser.add_argument("--keep", type=int, nargs="*", default=None,
                        help="id тех, кого оставить")
    parser.add_argument("--keep-none", action="store_true",
                        help="удалить вообще всех учеников")
    parser.add_argument("--performed-by", type=int, default=None,
                        help="id суперадмина, от чьего имени пишется аудит-лог")
    parser.add_argument("--apply", action="store_true",
                        help="выполнить (без флага — только показать)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            rows = _students(db, include_deleted=True)
            print(f"Учеников всего: {len(rows)}")
            for u in rows:
                print("  ", _display(u))
            return

        keep_ids = set(args.keep or [])
        if not keep_ids and not args.keep_none:
            print("Не указано, кого оставить. Задайте --keep <id> [<id> …] или --keep-none.")
            sys.exit(1)

        all_students = {u.id: u for u in _students(db, include_deleted=True)}
        unknown = sorted(keep_ids - set(all_students))
        if unknown:
            print(f"Эти id не найдены среди учеников: {unknown}")
            sys.exit(1)

        for uid in sorted(keep_ids):
            print(f"Остаётся: {_display(all_students[uid])}")

        targets = [u for u in _students(db) if u.id not in keep_ids]
        print(f"\nБудут удалены: {len(targets)}")
        for u in targets:
            print("  ", _display(u))

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
