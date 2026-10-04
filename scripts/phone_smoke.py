"""Локальный стенд для проверки глазами: приложение на файловой SQLite, без Postgres.

Зачем. `uvicorn app.main:app` требует настоящую базу, а `pytest` не показывает,
как экран выглядит и нажимается. До 29.09.2026 стенд писали заново в каждой
сессии аудита и держали во временной папке — этот файл заменяет те копии.

Что делает:
- подменяет `app.db.database.engine`/`SessionLocal` на файловую SQLite и вешает
  ту же заплатку часового пояса, что `tests/conftest.py` (SQLite теряет зону);
- глушит клиентов Telegram и планировщик пробников: в локальном `.env` лежат
  настоящие ключи, стенд не должен ничего отправлять наружу;
- снимает CSRF (`require_csrf`, `require_csrf_header`), чтобы POST можно было
  слать прямо из Playwright или curl;
- сеет настоящие роли (`seed_roles_and_permissions`), по сотруднику каждого
  ранга, учеников и работы у первого из них;
- заводит сессии и печатает их, затем поднимает `uvicorn`.

Сессии (кука `session_id`): `sess-sa` суперадмин, `sess-gp` Главный
преподаватель, `sess-mod` модератор, `sess-cur` куратор, `sess-st` ученик.

Запуск:

    cd portfolio-saas
    python scripts/phone_smoke.py                    # порт 8765, база во временной папке
    python scripts/phone_smoke.py --port 8777        # 8765 бывает занят стендом соседней сессии
    python scripts/phone_smoke.py --root <worktree>  # стенд на другой копии кода

`--root` нужен, когда в основной папке лежит чужой незакоммиченный код: сделать
`git worktree add --detach <папка> HEAD`, скопировать туда свои файлы и
запустить стенд на ней. Без `.env` в корне секрет сессии задаёт сам скрипт.

Данные под задачу (этап, задание, блоки) удобнее заводить через API под той же
кукой — CSRF снят, JSON принимается как от страницы:

    curl -b "session_id=sess-sa" -H "Content-Type: application/json" \\
         --data-binary @stage.json http://127.0.0.1:8765/cabinet/staff/program/stages

Кириллицу — из файла (`--data-binary @файл`): Git Bash на Windows портит её в
`-d '...'`, и сервер отвечает «There was an error parsing the body».

Дальше — Playwright с кукой `session_id`, для телефона `is_mobile=True,
has_touch=True`; ширина вбок — `scrollWidth - innerWidth`. Ловушка эмулятора:
после перезагрузки страницы отправкой формы Chromium начинает отвечать
`matchMedia('(hover: hover)') === true` — мерить на свежей загрузке.

Не отключено: загрузка файлов идёт в S3 по ключам из `.env`. Фото на стенде
не грузить, если это не проверяемое действие.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(tempfile.gettempdir()) / "apparchi_stand.db"
STUDENT_COUNT = 40
# Картинка из самого приложения: работы стенда не ходят в S3.
WORK_IMAGE = "/static/img/logo.webp?v=2"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Локальный стенд Apparchi на SQLite")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help="файл SQLite; пересоздаётся при каждом запуске")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="корень portfolio-saas, на котором поднять стенд")
    parser.add_argument("--students", type=int, default=STUDENT_COUNT)
    return parser.parse_args()


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) != 0


def main() -> None:
    args = _parse_args()
    root = args.root.resolve()
    if not (root / "app" / "main.py").exists():
        sys.exit(f"В {root} нет app/main.py — это не корень portfolio-saas")
    if not _port_is_free(args.host, args.port):
        sys.exit(f"Порт {args.port} занят (возможно, стенд другой сессии) — задайте --port")

    # До любого импорта приложения: settings читаются при импорте app.config.
    os.chdir(root)
    sys.path.insert(0, str(root))
    if args.db.exists():
        args.db.unlink()
    os.environ["DATABASE_URL"] = "sqlite:///" + args.db.as_posix()
    os.environ.setdefault("SESSION_SECRET", "stand")

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    import app.db.database as db_module

    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
    session_local = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db_module.engine = engine
    db_module.SessionLocal = session_local
    base = db_module.Base

    def attach_utc(instance) -> None:
        for attr in instance.__mapper__.column_attrs:
            for col in attr.columns:
                if getattr(col.type, "timezone", False):
                    value = getattr(instance, attr.key, None)
                    if isinstance(value, datetime) and value.tzinfo is None:
                        setattr(instance, attr.key, value.replace(tzinfo=timezone.utc))

    event.listen(base, "load", lambda instance, context: attach_utc(instance), propagate=True)
    event.listen(base, "refresh", lambda instance, context, attrs: attach_utc(instance), propagate=True)

    import app.services.exam_scheduler as exam_scheduler
    import app.services.telegram as telegram_service
    import app.services.telegram_login as telegram_login_service

    async def _noop() -> None:
        return None

    for service in (telegram_service, telegram_login_service):
        service.init_client = _noop
        service.close_client = _noop
    exam_scheduler.start_scheduler = lambda: None
    exam_scheduler.stop_scheduler = lambda: None

    from app.dependencies import require_csrf, require_csrf_header
    from app.main import app
    from app.models.role import Role
    from app.models.session import Session
    from app.models.user import User
    from app.models.work import Work
    from app.services.rbac import seed_roles_and_permissions

    app.dependency_overrides[require_csrf] = lambda: None
    app.dependency_overrides[require_csrf_header] = lambda: None

    base.metadata.create_all(engine)
    db = session_local()
    seed_roles_and_permissions(db)
    # Типы событий дайджеста на проде сеет миграция, а стенд строит схему
    # через create_all — без этого календарь месяца не дал бы добавить событие.
    from app.services.schedule_event_types import seed_default_types
    seed_default_types(db)
    roles = {role.name: role for role in db.query(Role).all()}

    staff = {
        "sess-sa": ("Суперадмин", "суперадмин"),
        "sess-gp": ("Главный преподаватель", "админ"),
        "sess-mod": ("Модератор", "модератор"),
        "sess-cur": ("Куратор", "куратор"),
    }
    users = {}
    for vk_id, (sid, (name, role_name)) in enumerate(staff.items(), start=1):
        user = User(vk_id=vk_id, name=name, role_id=roles[role_name].id,
                    is_active=True, profile_completed=True)
        db.add(user)
        users[sid] = user
    db.commit()
    curator = users["sess-cur"]

    students = []
    for index in range(args.students):
        # Первое имя длинное нарочно: на нём видно, как строка переносится на телефоне.
        name = ("Александра Константиновна Преображенская" if index == 0
                else f"Ученик {index:02d} Фамилия")
        students.append(User(
            vk_id=1000 + index, name=name, tariff="Я С ВАМИ", role_id=roles["ученик"].id,
            is_active=True, is_group_member=True, profile_completed=(index != 3),
            portfolio_do_completed=True, course_periods="10-14 июня", lessons_count="8",
            curator_id=curator.id,
        ))
    db.add_all(students)
    db.commit()
    first = students[0]
    users["sess-st"] = first

    now = datetime.now(timezone.utc)
    common = {"user_id": first.id, "month": "сентябрь", "year": now.year,
              "s3_url": WORK_IMAGE, "status": "success"}
    db.add_all([
        Work(work_type="before", filename="before.webp", **common),
        Work(work_type="after", filename="after.webp", **common),
        Work(work_type="mock_exam", subject="Рисунок", filename="mock1.webp", score=78, **common),
        Work(work_type="mock_exam", subject="Композиция", filename="mock2.webp", **common),
        Work(work_type="retake", subject="Рисунок", filename="retake.webp", **common),
    ])
    for sid, user in users.items():
        db.add(Session(id=sid, user_id=user.id, expires_at=now + timedelta(days=1), is_active=True))
    db.commit()
    db.close()

    url = f"http://{args.host}:{args.port}"
    print(f"Стенд: {url}  (код: {root}, база: {args.db})")
    print("Сессии (кука session_id): " + ", ".join(users))
    print(f"Ученик с работами: id={first.id}")
    sys.stdout.flush()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
