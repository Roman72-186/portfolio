"""
digest_event_times.py — перенос времени из названий событий дайджеста в поля.

До 05.10.2026 у события не было поля времени, и его вписывали в название:
«тренировка РИСУНОК 10:00-11:30», «Р+К очно 16:00-19:00» (прод 04.10.2026,
двадцать событий октябрьского дайджеста). Миграция `a206e833f766` завела
`time_from`/`time_to`; скрипт находит время в названии, ставит его в поля и
убирает из названия.

Берёт только явное время с двоеточием: диапазон «10:00-11:30»
(дефис, короткое или длинное тире, «до») или одиночное «в 19:00». Время
с точкой («10.11») не берётся — так пишут и даты. Событие,
у которого время уже стоит, не трогается — скрипт можно запускать повторно.
Название, от которого после переноса ничего не осталось, не трогается.

Запуск на проде (внутри контейнера, DATABASE_URL уже в окружении):

    docker exec -w /app -e PYTHONPATH=/app portfolio-saas-app-1 \
        python scripts/digest_event_times.py

    docker exec -w /app -e PYTHONPATH=/app portfolio-saas-app-1 \
        python scripts/digest_event_times.py --digest 2 --apply

Без `--apply` скрипт ничего не пишет: показывает «было → стало» по каждому
событию. Каждая правка пишется в журнал (`digest_event_update`), как правка
из редактора, — автором `--performed-by` (по умолчанию 199, владелец).
"""
import argparse
import json
import os
import re
import sys
from datetime import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import SessionLocal  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402
from app.models.tracker import ScheduleEvent  # noqa: E402
from app.services.tracker import format_event_time  # noqa: E402

_HM = r"(?P<{0}h>[01]?\d|2[0-3]):(?P<{0}m>[0-5]\d)"
RANGE_RE = re.compile(
    r"(?:\bс\s+)?" + _HM.format("a") + r"\s*(?:-|–|—|\bдо\b)\s*" + _HM.format("b"),
    re.IGNORECASE,
)
SINGLE_RE = re.compile(r"(?:\b(?:в|с)\s+)?" + _HM.format("a") + r"(?!\d)", re.IGNORECASE)


def split_title(title: str) -> tuple[str, time | None, time | None] | None:
    """(новое название, с, до) или None, если времени в названии нет."""
    match = RANGE_RE.search(title)
    time_to = None
    if match:
        time_to = time(int(match["bh"]), int(match["bm"]))
    else:
        match = SINGLE_RE.search(title)
        if not match:
            return None
    time_from = time(int(match["ah"]), int(match["am"]))
    rest = (title[:match.start()] + " " + title[match.end():])
    rest = re.sub(r"\s+([,;])", r"\1", re.sub(r"\s+", " ", rest)).strip(" -–—,;:")
    if not rest:
        return None
    return rest, time_from, time_to


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--digest", type=int, help="только события этого дайджеста")
    parser.add_argument("--apply", action="store_true", help="записать (без флага — только показать)")
    parser.add_argument("--performed-by", type=int, default=199, help="автор правки в журнале")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(ScheduleEvent).filter(ScheduleEvent.time_from.is_(None))
        if args.digest:
            query = query.filter(ScheduleEvent.digest_id == args.digest)
        changed = 0
        for event in query.order_by(ScheduleEvent.digest_id, ScheduleEvent.starts_on, ScheduleEvent.id):
            parsed = split_title(event.title)
            if parsed is None:
                continue
            new_title, time_from, time_to = parsed
            if time_to is not None and event.starts_on == event.ends_on and time_to <= time_from:
                print(f"  пропуск #{event.id} «{event.title}»: конец раньше начала")
                continue
            old_title = event.title
            event.title, event.time_from, event.time_to = new_title, time_from, time_to
            print(
                f"#{event.id} {event.starts_on:%d.%m} «{old_title}» → "
                f"«{new_title}», {format_event_time(event)}"
            )
            changed += 1
            if args.apply:
                db.add(AuditLog(
                    action="digest_event_update",
                    performed_by_id=args.performed_by,
                    details=json.dumps({
                        "digest_id": event.digest_id,
                        "event_id": event.id,
                        "title": event.title[:200],
                        "starts_on": event.starts_on.isoformat(),
                        "ends_on": event.ends_on.isoformat(),
                        "time": format_event_time(event),
                        "type": event.type.name,
                        "source": "scripts/digest_event_times.py",
                    }, ensure_ascii=False),
                ))
        if args.apply:
            db.commit()
            print(f"\nЗаписано: {changed}")
        else:
            db.rollback()
            print(f"\nБудет изменено: {changed}. Ничего не записано — для записи добавьте --apply.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
