"""Generate a one-time login link for the superadmin (roman.m).

Run inside the Docker container on the server:
    docker exec portfolio-saas-app-1 python scripts/generate_admin_link.py

Or with custom TTL (hours):
    docker exec portfolio-saas-app-1 python scripts/generate_admin_link.py --ttl-hours 48
"""
import argparse
import hashlib
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

STAFF_LOGIN = "roman.m"
DEFAULT_TTL_HOURS = 24


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a login link for superadmin")
    parser.add_argument("--ttl-hours", type=int, default=DEFAULT_TTL_HOURS,
                        help=f"Link lifetime in hours (default: {DEFAULT_TTL_HOURS})")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    domain = os.environ.get("DOMAIN", "kodarhitektora.ru")

    import psycopg2

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE staff_login = %s", (STAFF_LOGIN,))
    row = cur.fetchone()
    if not row:
        print(f"ERROR: User with staff_login='{STAFF_LOGIN}' not found.", file=sys.stderr)
        sys.exit(1)
    user_id = row[0]

    now = datetime.now(timezone.utc)

    # Revoke all active tokens for this user
    cur.execute(
        """
        UPDATE login_tokens
        SET revoked_at = %s
        WHERE user_id = %s
          AND used_at IS NULL
          AND revoked_at IS NULL
          AND expires_at > %s
        """,
        (now, user_id, now),
    )

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = now + timedelta(hours=args.ttl_hours)

    cur.execute(
        """
        INSERT INTO login_tokens (id, user_id, token_hash, issued_by, expires_at, created_at)
        VALUES (%s, %s, %s, 'admin-cli', %s, %s)
        """,
        (str(uuid.uuid4()), user_id, token_hash, expires_at, now),
    )
    conn.commit()
    cur.close()
    conn.close()

    link = f"https://{domain}/auth/link?token={quote(raw_token)}"
    print(f"\nСсылка для входа суперадмина (действует {args.ttl_hours}ч):\n")
    print(link)
    print()


if __name__ == "__main__":
    main()
