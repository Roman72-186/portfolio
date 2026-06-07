"""
reset_for_testing.py — очищает базу данных и S3 для тестирования с нуля.
Удаляет всё кроме суперадмина (is_admin=True или role.name='суперадмин').
Запускать: python scripts/reset_for_testing.py
"""
import os
import sys
import psycopg2
import boto3
from botocore.client import Config

DB_URL = os.environ.get(
    "DATABASE_URL",
    ""
)
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_REGION = os.environ.get("S3_REGION", "ru-1")

def confirm():
    print("\n⚠️  ВНИМАНИЕ: Это удалит всех пользователей (кроме суперадмина) и все загруженные фото из БД и S3!")
    ans = input("Введите YES для подтверждения: ").strip()
    if ans != "YES":
        print("Отменено.")
        sys.exit(0)

def get_superadmin_ids(cur):
    cur.execute("""
        SELECT u.id FROM users u
        LEFT JOIN roles r ON u.role_id = r.id
        WHERE r.name = 'суперадмин' OR u.is_admin = true
    """)
    return [r[0] for r in cur.fetchall()]

def get_s3_paths_to_delete(cur, keep_user_ids):
    if keep_user_ids:
        cur.execute(
            "SELECT s3_path FROM works WHERE user_id NOT IN %s AND s3_path IS NOT NULL",
            (tuple(keep_user_ids),)
        )
    else:
        cur.execute("SELECT s3_path FROM works WHERE s3_path IS NOT NULL")
    return [r[0] for r in cur.fetchall()]

def delete_s3_files(paths):
    if not paths:
        print("S3: нет файлов для удаления.")
        return
    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
        config=Config(signature_version="s3v4"),
    )
    objects = [{"Key": p} for p in paths]
    for i in range(0, len(objects), 1000):
        batch = objects[i:i+1000]
        resp = s3.delete_objects(Bucket=S3_BUCKET, Delete={"Objects": batch})
        deleted = len(resp.get("Deleted", []))
        errors = resp.get("Errors", [])
        print(f"S3: удалено {deleted}, ошибок {len(errors)}")
        for e in errors:
            print(f"  Ошибка: {e['Key']} — {e['Message']}")

def reset_db(cur, keep_ids):
    keep_tuple = tuple(keep_ids)

    # 1. Удаляем ВСЕ уведомления (могут ссылаться на works чужих пользователей)
    cur.execute("DELETE FROM notifications")
    print("  notifications: очищено")

    # 2. Exam assignments (не привязаны к user_id напрямую)
    for t in ["exam_ticket_assignees", "exam_tickets", "exam_assignments"]:
        try:
            cur.execute(f"DELETE FROM {t}")
            print(f"  {t}: очищено")
        except Exception as e:
            print(f"  {t}: пропущено ({e})")

    # 3. Работы (фотографии) — только чужих пользователей
    cur.execute("DELETE FROM works WHERE user_id NOT IN %s", (keep_tuple,))
    print("  works: очищено")

    # 4. Мелкие таблицы с user_id
    for t in ["mock_exam_locks", "login_tokens", "sessions", "upload_log"]:
        try:
            cur.execute(f"DELETE FROM {t} WHERE user_id NOT IN %s", (keep_tuple,))
            print(f"  {t}: очищено")
        except Exception as e:
            print(f"  {t}: пропущено ({e})")

    # 5. feature_periods: переназначить created_by_id на суперадмина (NOT NULL ограничение)
    superadmin_id = keep_ids[0]
    cur.execute(
        "UPDATE feature_periods SET created_by_id = %s WHERE created_by_id NOT IN %s",
        (superadmin_id, keep_tuple)
    )
    print(f"  feature_periods.created_by_id: переназначено на суперадмина (id={superadmin_id})")

    # 6. Удаляем пользователей
    cur.execute("DELETE FROM users WHERE id NOT IN %s", (keep_tuple,))
    print(f"  users: очищены (сохранены id={list(keep_ids)})")

def main():
    confirm()
    missing = [
        name for name, value in {
            "DATABASE_URL": DB_URL,
            "S3_ENDPOINT": S3_ENDPOINT,
            "S3_BUCKET": S3_BUCKET,
            "S3_ACCESS_KEY": S3_ACCESS_KEY,
            "S3_SECRET_KEY": S3_SECRET_KEY,
        }.items()
        if not value
    ]
    if missing:
        print(f"❌ Не заданы переменные окружения: {', '.join(missing)}")
        sys.exit(1)

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    print("\n🔍 Определяем суперадминов...")
    keep_ids = get_superadmin_ids(cur)
    if not keep_ids:
        print("❌ Суперадмин не найден! Прерываем для безопасности.")
        sys.exit(1)
    print(f"  Сохраняем user.id = {keep_ids}")

    print("\n📦 Собираем S3 пути для удаления...")
    s3_paths = get_s3_paths_to_delete(cur, keep_ids)
    print(f"  Найдено {len(s3_paths)} файлов в S3")

    print("\n🗄️  Очищаем базу данных...")
    try:
        reset_db(cur, keep_ids)
        conn.commit()
        print("  ✅ БД очищена и закоммичена")
    except Exception as e:
        conn.rollback()
        print(f"  ❌ Ошибка: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()

    print("\n🗑️  Удаляем файлы из S3...")
    delete_s3_files(s3_paths)

    print("\n✅ Готово! База очищена, тестируемся с нуля.")

if __name__ == "__main__":
    main()
