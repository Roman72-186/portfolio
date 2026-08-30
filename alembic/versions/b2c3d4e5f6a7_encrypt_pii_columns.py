"""encrypt PII columns (phone, parent_phone, tg_username)

Widen columns to TEXT to accommodate Fernet ciphertext,
then encrypt existing plaintext values in-place.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-16 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ни одна миграция в этой цепочке не заводит `parent_phone`/`deleted_at`/
    # `university_year` на `users`, и таблицы `feature_periods`/`audit_logs`
    # целиком — на проде всё это когда-то добавили вручную мимо Alembic
    # (найдено 30.08.2026 при поднятии чистого docker-compose: сверка живой
    # схемы контейнера с `Base.metadata` через `sqlalchemy.inspect` — та же
    # история, что раньше уже блокировала свежий docker-compose на одном
    # `parent_phone`, см. TODO.md, но пробелов оказалось больше одного).
    # На проде всё уже есть — операции no-op; на свежей базе — восполняем.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS parent_phone VARCHAR(30)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS university_year INTEGER")
    op.execute(
        "CREATE TABLE IF NOT EXISTS feature_periods ("
        "id SERIAL PRIMARY KEY, "
        "feature VARCHAR(30) NOT NULL, "
        "title VARCHAR(100), "
        "start_date DATE NOT NULL, "
        "end_date DATE NOT NULL, "
        "is_active BOOLEAN NOT NULL DEFAULT true, "
        "created_by_id INTEGER NOT NULL REFERENCES users(id), "
        "created_at TIMESTAMPTZ DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_feature_periods_feature_active "
        "ON feature_periods (feature, is_active)"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS audit_logs ("
        "id SERIAL PRIMARY KEY, "
        "action VARCHAR(50) NOT NULL, "
        "performed_by_id INTEGER NOT NULL REFERENCES users(id), "
        "target_user_id INTEGER REFERENCES users(id), "
        "details VARCHAR(1000), "
        "created_at TIMESTAMPTZ DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_performed_by "
        "ON audit_logs (performed_by_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_target_user "
        "ON audit_logs (target_user_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_action_created "
        "ON audit_logs (action, created_at)"
    )

    # Widen columns to TEXT to fit Fernet tokens (~120+ chars)
    op.alter_column('users', 'phone', type_=sa.Text(), existing_nullable=True)
    op.alter_column('users', 'parent_phone', type_=sa.Text(), existing_nullable=True)
    op.alter_column('users', 'tg_username', type_=sa.Text(), existing_nullable=True)

    # Encrypt existing plaintext values
    from app.crypto import _get_fernet
    f = _get_fernet()

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, phone, parent_phone, tg_username FROM users")).fetchall()
    for row in rows:
        updates = {}
        if row.phone and not row.phone.startswith("gAAAAA"):
            updates["phone"] = f.encrypt(row.phone.encode()).decode()
        if row.parent_phone and not row.parent_phone.startswith("gAAAAA"):
            updates["parent_phone"] = f.encrypt(row.parent_phone.encode()).decode()
        if row.tg_username and not row.tg_username.startswith("gAAAAA"):
            updates["tg_username"] = f.encrypt(row.tg_username.encode()).decode()
        if updates:
            set_clause = ", ".join(f"{k} = :v_{k}" for k in updates)
            params = {f"v_{k}": v for k, v in updates.items()}
            params["uid"] = row.id
            conn.execute(sa.text(f"UPDATE users SET {set_clause} WHERE id = :uid"), params)


def downgrade() -> None:
    # Decrypt values back to plaintext
    from app.crypto import _get_fernet
    from cryptography.fernet import InvalidToken
    f = _get_fernet()

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, phone, parent_phone, tg_username FROM users")).fetchall()
    for row in rows:
        updates = {}
        for col in ("phone", "parent_phone", "tg_username"):
            val = getattr(row, col)
            if val:
                try:
                    updates[col] = f.decrypt(val.encode()).decode()
                except (InvalidToken, Exception):
                    pass
        if updates:
            set_clause = ", ".join(f"{k} = :v_{k}" for k in updates)
            params = {f"v_{k}": v for k, v in updates.items()}
            params["uid"] = row.id
            conn.execute(sa.text(f"UPDATE users SET {set_clause} WHERE id = :uid"), params)

    op.alter_column('users', 'phone', type_=sa.String(30), existing_nullable=True)
    op.alter_column('users', 'parent_phone', type_=sa.String(30), existing_nullable=True)
    op.alter_column('users', 'tg_username', type_=sa.String(100), existing_nullable=True)
