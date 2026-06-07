"""
One-time script: create a superadmin account for Roman Makhmetov.

Run inside the Docker container on the server:
    docker exec portfolio-saas python scripts/create_superadmin.py
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

# ── credentials ──────────────────────────────────────────────────────────────
STAFF_LOGIN = os.environ.get("SUPERADMIN_STAFF_LOGIN", "roman.m")
STAFF_PASSWORD = os.environ.get("SUPERADMIN_STAFF_PASSWORD", "")
FIRST_NAME = os.environ.get("SUPERADMIN_FIRST_NAME", "Роман")
LAST_NAME = os.environ.get("SUPERADMIN_LAST_NAME", "Махметов")
# ─────────────────────────────────────────────────────────────────────────────


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL env var is not set", file=sys.stderr)
        sys.exit(1)
    if not STAFF_PASSWORD:
        print("ERROR: SUPERADMIN_STAFF_PASSWORD env var is not set", file=sys.stderr)
        sys.exit(1)

    import psycopg2
    import bcrypt as _bcrypt

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # Check if login already exists
    cur.execute("SELECT id FROM users WHERE staff_login = %s", (STAFF_LOGIN,))
    if cur.fetchone():
        print(f"Account with login '{STAFF_LOGIN}' already exists — nothing to do.")
        cur.close()
        conn.close()
        return

    # Get superadmin role id (rank = 5)
    cur.execute("SELECT id FROM roles WHERE name = 'суперадмин'")
    row = cur.fetchone()
    if not row:
        print("ERROR: 'суперадмин' role not found. Run the app once to seed roles.", file=sys.stderr)
        cur.close()
        conn.close()
        sys.exit(1)
    role_id = row[0]

    # Find the lowest vk_id to assign a unique negative placeholder
    cur.execute("SELECT MIN(vk_id) FROM users")
    min_vk = cur.fetchone()[0] or 0
    new_vk_id = min(min_vk - 1, -1)

    full_name = f"{FIRST_NAME} {LAST_NAME}"
    password_hash = _bcrypt.hashpw(STAFF_PASSWORD.encode(), _bcrypt.gensalt()).decode()

    cur.execute(
        """
        INSERT INTO users (vk_id, name, first_name, last_name,
                           staff_login, password_hash, role_id,
                           is_active, is_admin, is_group_member,
                           tariff, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s,
                TRUE, TRUE, FALSE,
                'УВЕРЕННЫЙ', NOW(), NOW())
        RETURNING id
        """,
        (new_vk_id, full_name, FIRST_NAME, LAST_NAME,
         STAFF_LOGIN, password_hash, role_id),
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    print("=" * 50)
    print(f"Superadmin created  (id={new_id})")
    print(f"  Name   : {full_name}")
    print(f"  Login  : {STAFF_LOGIN}")
    print("  Password: <set from SUPERADMIN_STAFF_PASSWORD>")
    print(f"  Role   : суперадмин (rank 5)")
    print(f"  Login URL: https://apparchi.ru/auth/staff/login")
    print("=" * 50)


if __name__ == "__main__":
    main()
