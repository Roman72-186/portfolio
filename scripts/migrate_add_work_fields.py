"""
Migration: add new columns to users table + create works table.

Run on the VPS:
    docker exec portfolio-saas-app-1 python scripts/migrate_add_work_fields.py
Or locally (with DATABASE_URL pointing to Postgres):
    python scripts/migrate_add_work_fields.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2


def get_conn():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL env var is not set")
    # psycopg2 doesn't accept the postgresql:// scheme prefix directly in some versions
    return psycopg2.connect(url)


def column_exists(cur, table, column):
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
        (table, column),
    )
    return cur.fetchone() is not None


def table_exists(cur, table):
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name=%s",
        (table,),
    )
    return cur.fetchone() is not None


def run():
    conn = get_conn()
    cur = conn.cursor()

    # ── 1. users: new columns ─────────────────────────────────────────────
    additions = [
        ("drive_folder_id",        "VARCHAR(200)"),
        ("portfolio_do_completed", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("curator_id",             "INTEGER REFERENCES users(id)"),
    ]
    for col, typedef in additions:
        if not column_exists(cur, "users", col):
            cur.execute(f"ALTER TABLE users ADD COLUMN {col} {typedef}")
            print(f"  + users.{col}")
        else:
            print(f"  = users.{col} already exists")

    # ── 2. works table ───────────────────────────────────────────────────
    if not table_exists(cur, "works"):
        cur.execute("""
            CREATE TABLE works (
                id             SERIAL PRIMARY KEY,
                user_id        INTEGER NOT NULL REFERENCES users(id),
                work_type      VARCHAR(20) NOT NULL,
                month          VARCHAR(20) NOT NULL,
                year           INTEGER NOT NULL,
                filename       VARCHAR(255) NOT NULL,
                s3_url         VARCHAR(500),
                s3_path        VARCHAR(300),
                drive_file_id  VARCHAR(200),
                score          NUMERIC(5,2),
                scored_at      TIMESTAMP WITH TIME ZONE,
                scored_by_id   INTEGER REFERENCES users(id),
                status         VARCHAR(20) NOT NULL DEFAULT 'pending',
                created_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX ix_works_user_type ON works(user_id, work_type)")
        cur.execute("CREATE INDEX ix_works_user_year_month ON works(user_id, year, month)")
        print("  + table works created")
    else:
        print("  = table works already exists")

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    run()
