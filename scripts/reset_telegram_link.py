"""
reset_telegram_link.py — отвязать Telegram-аккаунт от одного ученика, чтобы
следующий вход через бота завёл СОВЕРШЕННО НОВЫЙ профиль (новый id, анкета
с нуля, без куратора/тарифа/истории), а не воскресил старый.

Почему не `delete_students.py` (soft-delete): `_upsert_telegram_user`
(app/api/auth.py) matчит вход по `telegram_chat_id`. Если строка с этим
`telegram_chat_id` осталась в базе хоть с `deleted_at` — на новом /start она
просто снимет deleted_at и вернёт СТАРЫЙ профиль (см. докстринг
delete_students.py). Чтобы бот создал новую строку, старая не должна больше
матчиться ни по `telegram_chat_id`, ни по `tg_username` — второе поле само по
себе не участвует в логине, но участвует в проверке уникальности ника на
/cabinet/personal/contacts (find_student_by_tg_username, cabinet_personal.py:213):
не обнулить его — и новый профиль с тем же ником, который Telegram подставит
сам, откажется сохраняться с ошибкой «ник уже занят другим учеником».

Ничего не удаляется: работы, оценки, переписки, куратор, тариф у СТАРОЙ
строки остаются как есть — просто у неё отбирается ключ, по которому её
находит бот. Полностью обратимо: скрипт печатает прежние значения, их можно
вернуть вручную через UPDATE, если понадобится.

Запуск на проде (внутри контейнера, DATABASE_URL уже в окружении):

    docker exec portfolio-saas-app-1 python scripts/reset_telegram_link.py --id 168

    docker exec portfolio-saas-app-1 python scripts/reset_telegram_link.py --id 168 --apply

Без `--apply` скрипт ничего не пишет: показывает, что бы изменил.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.cache import invalidate_session  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.role import Role  # noqa: E402
from app.models.session import Session  # noqa: E402
from app.models.user import User  # noqa: E402

SUPERADMIN_RANK = 5


def _invalidate_sessions(db, user_id: int) -> None:
    sessions = (
        db.query(Session)
        .filter(Session.user_id == user_id, Session.is_active == True)  # noqa: E712
        .all()
    )
    for s in sessions:
        s.is_active = False
        invalidate_session(s.id)


def _display(u: User) -> str:
    name = f"{u.last_name or ''} {u.first_name or u.name}".strip()
    tg_chat = u.telegram_chat_id or "—"
    tg_user = u.tg_username or "—"
    state = "удалён" if u.deleted_at else ("активен" if u.is_active else "заблокирован")
    return (f"id={u.id:<6} {name:<26} telegram_chat_id={tg_chat!s:<14} "
            f"tg_username={tg_user:<20} {state}")


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
        description="Отвязать Telegram от ученика, чтобы вход создал новый профиль"
    )
    parser.add_argument("--id", type=int, required=True, help="id ученика")
    parser.add_argument("--performed-by", type=int, default=None,
                        help="id суперадмина, от чьего имени пишется аудит-лог")
    parser.add_argument("--apply", action="store_true",
                        help="выполнить (без флага — только показать)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == args.id).first()
        if not user:
            print(f"id={args.id} не найден")
            sys.exit(1)

        print("Сейчас:")
        print("  ", _display(user))

        if not user.telegram_chat_id and not user.tg_username:
            print("\nУже отвязан: telegram_chat_id и tg_username пустые. Делать нечего.")
            return

        if not args.apply:
            print("\nБудет обнулено: telegram_chat_id, tg_username, tg_username_mismatch.")
            print("Активные сессии будут завершены.")
            print("Это прогон без записи. Повторите с --apply, чтобы выполнить.")
            return

        actor = _resolve_actor(db, args.performed_by)
        if actor is None:
            sys.exit(1)

        old_chat_id = user.telegram_chat_id
        old_username = user.tg_username

        user.telegram_chat_id = None
        user.tg_username = None
        user.tg_username_mismatch = False

        _invalidate_sessions(db, user.id)
        db.add(AuditLog(
            action="telegram_unlink",
            performed_by_id=actor.id,
            target_user_id=user.id,
            details=(f"Отвязан Telegram у id={user.id} ({user.name}): "
                     f"telegram_chat_id {old_chat_id!s} → NULL, "
                     f"tg_username {old_username or '—'} → NULL"),
        ))
        db.commit()

        print("\nГотово. Прежние значения (на случай отката):")
        print(f"   telegram_chat_id={old_chat_id!s}  tg_username={old_username or '—'}")
        print("Следующий вход этим Telegram-аккаунтом создаст новый профиль с нуля.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
