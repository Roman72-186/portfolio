"""
clear_dashboard_stats.py — обнуление статистики кабинетов Суперадмина и
Главного преподавателя.

Отдельных счётчиков в системе нет: и сводка ГП, и обе страницы суперадмина
(«Статистика» и «Активность») считаются на лету из живых записей. Поэтому
обнулить их можно только удалив то, что они складывают.

Удаление жёсткое, без права на «снять флажок»: строки исчезают из базы.
Точка отката — дамп PostgreSQL, снимать ДО запуска:

    docker exec portfolio-saas-db-1 sh -lc \
        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
        | gzip > /root/pre-stats-clear-$(date -u +%Y%m%dT%H%M%SZ).sql.gz

Что удаляется — всё, что порождено учебной активностью:
работы и оценки, диалоги обратной связи с сообщениями и фото, домашние
работы с проверками, экзаменационные циклы, попытки и блокировки пробников,
уведомления, видео-отчёты кураторов, одноразовые ссылки входа, журнал
действий.

Что НЕ трогается: люди и их профили (включая отметки онбординга), теги и
когорты, экзаменационные задания и билеты, периоды, учебная программа и
трекер, видеомодуль с просмотрами, анкеты и тесты с ответами, гостевой
пробник, файлы в S3 и на Google Drive. Гостевой модуль исключён намеренно —
у него своя статистика и свой запрет на снос (см. AGENTS.md).

Файлы в хранилище остаются: скрипт чистит только базу. Ссылки на них
пропадают вместе со строками, поэтому вернуть работы можно лишь из дампа.

Запуск на проде (внутри контейнера, DATABASE_URL уже в окружении):

    docker exec portfolio-saas-app-1 python scripts/clear_dashboard_stats.py
    docker exec portfolio-saas-app-1 python scripts/clear_dashboard_stats.py --apply

Без `--apply` скрипт ничего не пишет: показывает, что и сколько удалит.
"""
import argparse
import os
import sys

# Порядок важен: сначала дети, потом родители. `works.cycle_id` смотрит на
# exam_cycles, а `feedbacks.work_id` — на works, и обе связи без каскада.
TABLES = [
    ("feedback_messages", "сообщения обратной связи"),
    ("feedback_photos", "фото в обратной связи"),
    ("feedbacks", "диалоги обратной связи"),
    ("homework_feedback_messages", "сообщения по домашним работам"),
    ("homework_feedbacks", "проверки домашних работ"),
    ("homework_submission_images", "фото домашних работ"),
    ("homework_submissions", "сданные домашние работы"),
    ("notifications", "уведомления"),
    ("mock_exam_attempts", "попытки пробников"),
    ("mock_exam_locks", "блокировки пробников"),
    ("curator_reports", "видео-отчёты кураторов"),
    ("login_tokens", "одноразовые ссылки входа"),
    ("works", "работы и оценки"),
    ("exam_cycles", "экзаменационные циклы"),
    ("audit_logs", "журнал действий"),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Обнулить статистику кабинетов Суперадмина и Главного преподавателя"
    )
    parser.add_argument("--apply", action="store_true",
                        help="выполнить (без флага — только показать)")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    except ImportError:
        pass

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL не задан", file=sys.stderr)
        sys.exit(1)

    import psycopg2
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    counts = {}
    for table, label in TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]

    total = sum(counts.values())
    print("Будет удалено:")
    for table, label in TABLES:
        print(f"  {label:34} {counts[table]}")
    print(f"  {'ИТОГО строк':34} {total}")

    if not total:
        print("\nУдалять нечего, статистика уже пустая.")
        conn.close()
        return

    if not args.apply:
        print("\nЭто прогон без записи. Повторите с --apply, чтобы выполнить.")
        conn.close()
        return

    # Одной транзакцией: если внешний ключ где-то не пустит, откатится всё,
    # а не половина статистики.
    try:
        for table, _ in TABLES:
            cur.execute(f"DELETE FROM {table}")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"\nОшибка, ничего не удалено: {exc}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    print("\nОсталось после очистки:")
    for table, label in TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        print(f"  {label:34} {cur.fetchone()[0]}")

    cur.close()
    conn.close()
    print("\nГотово. Дашборды считают с нуля.")
    print("Люди, задания, программа, видео и гостевой пробник не тронуты.")


if __name__ == "__main__":
    main()
