"""
archive_students.py — массовая отправка учеников в архив перед новым потоком.

Архив = `users.archived_at` + `is_active=False`: ученик уходит из рабочих
списков и теряет вход, а его работы, оценки и переписки остаются целыми и
открыты суперадмину на `/cabinet/students/archive`.

Трогает только роль «ученик» (rank=1). Персонал, кураторы и админы не
затрагиваются. Уже архивные пропускаются — скрипт можно запускать повторно.

Запуск на проде (внутри контейнера, DATABASE_URL уже в окружении):

    docker compose -f docker-compose.prod-ru.yml exec app \
        python scripts/archive_students.py --list

    docker compose -f docker-compose.prod-ru.yml exec app \
        python scripts/archive_students.py --find Махметов

    docker compose -f docker-compose.prod-ru.yml exec app \
        python scripts/archive_students.py --keep 42 --performed-by 1

    docker compose -f docker-compose.prod-ru.yml exec app \
        python scripts/archive_students.py --keep 42 --performed-by 1 --apply

Без `--apply` скрипт ничего не пишет: показывает, кого бы отправил в архив.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import SessionLocal  # noqa: E402
from app.models.role import Role  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.user_management import archive_user  # noqa: E402

STUDENT_RANK = 1
SUPERADMIN_RANK = 5


def _display(u: User) -> str:
    name = f"{u.last_name or ''} {u.first_name or u.name}".strip()
    state = "в архиве" if u.archived_at else ("активен" if u.is_active else "заблокирован")
    return f"id={u.id:<6} {name:<40} тариф={u.tariff or '—':<12} {state}"


def _students(db, *, include_archived: bool = False):
    q = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == STUDENT_RANK, User.deleted_at.is_(None))
    )
    if not include_archived:
        q = q.filter(User.archived_at.is_(None))
    return q.order_by(User.last_name, User.first_name).all()


def _resolve_actor(db, performed_by: int | None) -> User | None:
    if performed_by:
        actor = db.query(User).filter(User.id == performed_by).first()
        if not actor:
            print(f"❌ Пользователь id={performed_by} не найден")
            return None
        rank = actor.role.rank if actor.role else 0
        if rank < SUPERADMIN_RANK:
            print(f"❌ id={performed_by} — не суперадмин (ранг {rank})")
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
    print("❌ Суперадминов несколько или нет — укажите --performed-by <id>:")
    for c in candidates:
        print("   ", _display(c))
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Отправить учеников прошлого потока в архив")
    parser.add_argument("--list", action="store_true", help="показать всех действующих учеников и выйти")
    parser.add_argument("--find", default="", help="найти ученика по части имени и выйти")
    parser.add_argument("--keep", type=int, nargs="*", default=None,
                        help="id учеников, которых оставить действующими")
    parser.add_argument("--keep-none", action="store_true", help="архивировать вообще всех учеников")
    parser.add_argument("--performed-by", type=int, default=None,
                        help="id суперадмина, от чьего имени пишется аудит-лог")
    parser.add_argument("--apply", action="store_true", help="выполнить (без флага — только показать)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            rows = _students(db, include_archived=True)
            print(f"Учеников всего (кроме удалённых): {len(rows)}")
            for u in rows:
                print("  ", _display(u))
            return

        if args.find:
            needle = args.find.strip().lower()
            rows = [
                u for u in _students(db, include_archived=True)
                if needle in f"{u.last_name or ''} {u.first_name or ''} {u.name or ''}".lower()
            ]
            print(f"Совпадений по «{args.find}»: {len(rows)}")
            for u in rows:
                print("  ", _display(u))
            return

        keep_ids = set(args.keep or [])
        if not keep_ids and not args.keep_none:
            print("❌ Не указано, кого оставить. Задайте --keep <id> [<id> …] или --keep-none.")
            sys.exit(1)

        # Проверяем, что все --keep — действительно ученики: опечатка в id
        # означала бы, что нужного человека тоже отправят в архив.
        all_students = {u.id: u for u in _students(db, include_archived=True)}
        unknown = sorted(keep_ids - set(all_students))
        if unknown:
            print(f"❌ Эти id не найдены среди учеников: {unknown}")
            sys.exit(1)

        for uid in sorted(keep_ids):
            print(f"Останется действующим: {_display(all_students[uid])}")

        targets = [u for u in _students(db) if u.id not in keep_ids]
        print(f"\nВ архив пойдут: {len(targets)}")
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
            if archive_user(db, target_user_id=u.id, performed_by_id=actor.id, commit=False):
                done += 1
            else:
                failed.append(u.id)
        db.commit()

        print(f"\n✅ Отправлено в архив: {done}")
        if failed:
            print(f"⚠️ Не удалось (проверьте роль и права): {failed}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
