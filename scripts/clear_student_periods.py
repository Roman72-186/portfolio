"""
clear_student_periods.py — обнуляет course_periods и lessons_count
у всех текущих учеников (role.rank=1), чтобы они скрылись из списков.

После запуска ученики снова появятся только когда сами выберут
периоды и количество занятий в профиле.

Запуск:
    Установить DATABASE_URL, затем:
    python scripts/clear_student_periods.py
"""
import os
import sys
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def main() -> None:
    if not DATABASE_URL:
        print("❌ DATABASE_URL не задан")
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*) FROM users u
        JOIN roles r ON u.role_id = r.id
        WHERE r.rank = 1
          AND (u.course_periods IS NOT NULL OR u.lessons_count IS NOT NULL)
    """)
    (count,) = cur.fetchone()
    print(f"Учеников с заполненными периодами/занятиями: {count}")

    if count == 0:
        print("Нечего обнулять. Выход.")
        cur.close()
        conn.close()
        return

    ans = input(f"Обнулить course_periods и lessons_count у {count} учеников? (YES/no): ").strip()
    if ans != "YES":
        print("Отменено.")
        cur.close()
        conn.close()
        return

    cur.execute("""
        UPDATE users
        SET course_periods = NULL,
            lessons_count  = NULL
        WHERE role_id IN (SELECT id FROM roles WHERE rank = 1)
          AND (course_periods IS NOT NULL OR lessons_count IS NOT NULL)
    """)
    updated = cur.rowcount
    conn.commit()
    print(f"✅ Обновлено строк: {updated}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
