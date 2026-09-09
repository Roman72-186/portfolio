"""
set_tariffs_evenly.py — равномерно распределить действующих учеников по трём
новым тарифам (владелец 10.09.2026: учеников на проде нет живых — курс ещё не
стартовал, все аккаунты тестовые).

Трогает только роль «ученик» (rank=1), не архивных и не удалённых. Порядок —
по id, тариф назначается по кругу: TARIFFS_CURRENT[i % 3]. Каждая фактическая
смена логируется через log_tariff_change и сбрасывает активные сессии/кэш
ученика (тот же путь, что ручное редактирование в /cabinet/superadmin/users).

Запуск на проде (внутри контейнера, DATABASE_URL уже в окружении):

    docker compose -f docker-compose.prod-ru.yml exec app \
        python scripts/set_tariffs_evenly.py --list

    docker compose -f docker-compose.prod-ru.yml exec app \
        python scripts/set_tariffs_evenly.py --performed-by 1

    docker compose -f docker-compose.prod-ru.yml exec app \
        python scripts/set_tariffs_evenly.py --performed-by 1 --apply

Без --apply скрипт ничего не пишет: показывает, кому какой тариф достанется.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.constants import TARIFFS_CURRENT  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.models.role import Role  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.user_management import _invalidate_user_sessions, log_tariff_change  # noqa: E402

STUDENT_RANK = 1
SUPERADMIN_RANK = 5


def _display(u: User, new_tariff: str | None = None) -> str:
    name = f"{u.last_name or ''} {u.first_name or u.name}".strip()
    arrow = f" → {new_tariff}" if new_tariff and new_tariff != u.tariff else ""
    return f"id={u.id:<6} {name:<40} тариф={u.tariff or '—':<20}{arrow}"


def _students(db):
    return (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == STUDENT_RANK, User.deleted_at.is_(None), User.archived_at.is_(None))
        .order_by(User.id)
        .all()
    )


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
    parser = argparse.ArgumentParser(description="Распределить учеников поровну по трём тарифам")
    parser.add_argument("--list", action="store_true", help="показать всех действующих учеников и выйти")
    parser.add_argument("--performed-by", type=int, default=None,
                         help="id суперадмина, от чьего имени пишется аудит-лог")
    parser.add_argument("--apply", action="store_true", help="выполнить (без флага — только показать)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        students = _students(db)

        if args.list:
            print(f"Учеников всего (действующих): {len(students)}")
            for u in students:
                print("  ", _display(u))
            return

        assignments = [
            (u, TARIFFS_CURRENT[i % len(TARIFFS_CURRENT)])
            for i, u in enumerate(students)
        ]
        changed = [(u, t) for u, t in assignments if u.tariff != t]

        print(f"Учеников всего: {len(students)}, тарифы: {TARIFFS_CURRENT}")
        for u, t in assignments:
            print("  ", _display(u, t))
        print(f"\nФактических изменений: {len(changed)}")

        if not args.apply:
            print("\nЭто прогон без записи. Повторите с --apply, чтобы выполнить.")
            return

        actor = _resolve_actor(db, args.performed_by)
        if actor is None:
            sys.exit(1)

        for u, t in changed:
            log_tariff_change(db, actor.id, u.id, u.tariff, t)
            u.tariff = t
        db.commit()

        for u, _t in changed:
            _invalidate_user_sessions(db, u.id)
        db.commit()

        print(f"\n✅ Тариф изменён у {len(changed)} учеников")
    finally:
        db.close()


if __name__ == "__main__":
    main()
