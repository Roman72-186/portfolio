"""Очистка всех дней учебной программы: элементы уходят в мягкое удаление.

День программы — это не сущность, а московская дата у `TrackerTask.due_at`
(app/services/program.py). Поэтому «очистить дни» означает погасить все задачи
с проставленным `due_at`.

Удаление мягкое, ровно как в `app/services/tracker.py::delete_task`:
`deleted_at = now()` плюс `is_published = false`. Календарь и страница дня
фильтруют по `deleted_at IS NULL`, так что дни станут пустыми, а привязанные
ролики освободятся сами (`video_bindings` отдаёт «Освободился: элемент дня
удалили»). Строки `tracker_task_states` остаются — отчётность не теряется,
и операцию можно откатить, сняв `deleted_at`.

Что скрипт НЕ трогает: цели (`tracker_goals`), месячное расписание
(`schedule_digests`/`schedule_events`), темы недель, ролики, домашние работы,
анкеты и тесты, билеты пробников. Как и кнопка «удалить элемент» в кабинете.

Запуск внутри контейнера:
    docker exec portfolio-saas-app-1 python scripts/clear_program_days.py
Показать, что будет удалено, ничего не меняя:
    docker exec portfolio-saas-app-1 python scripts/clear_program_days.py --dry-run
"""
import os
import sys


def main():
    dry_run = "--dry-run" in sys.argv

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    except ImportError:
        pass

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import psycopg2
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT kind, (topic_id IS NULL) AS standalone, count(*)
        FROM tracker_tasks
        WHERE deleted_at IS NULL AND due_at IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    )
    rows = cur.fetchall()
    total = sum(r[2] for r in rows)

    if not total:
        print("Дни программы уже пусты, удалять нечего.")
        cur.close()
        conn.close()
        return

    print(f"Элементов в днях программы: {total}")
    for kind, standalone, count in rows:
        where = "вне недели" if standalone else "в неделе"
        print(f"  {kind} ({where}): {count}")

    if dry_run:
        print("\n--dry-run: ничего не изменено.")
        cur.close()
        conn.close()
        return

    cur.execute(
        """
        UPDATE tracker_tasks
        SET deleted_at = now(), is_published = false
        WHERE deleted_at IS NULL AND due_at IS NOT NULL
        """
    )
    updated = cur.rowcount
    conn.commit()

    cur.close()
    conn.close()
    print(f"\nОчищено элементов: {updated}. Дни программы пусты.")
    print("Состояния учеников, ролики, домашки, анкеты и расписание не тронуты.")


if __name__ == "__main__":
    main()
