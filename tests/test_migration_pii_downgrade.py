"""Откат шифрования ПДн (`b2c3d4e5f6a7`) не оставляет шифротекст молча.

Код-ревью 28.09.2026 (P3): `downgrade` глотал любую ошибку расшифровки —
`except (InvalidToken, Exception): pass`. Значения, зашифрованные прежним
ключом (`session_secret`, до появления `pii_encryption_secret`), он не
расшифровывал вовсе: чтение из базы пробует оба ключа, откат — только текущий.
А при чужом ключе откат проходил «успешно»: короткий `tg_username` в шифре —
ровно 100 символов и влезает в `VARCHAR(100)`, на месте ника остаётся
шифротекст.

Тест гоняет настоящий `downgrade()` на SQLite, подменив `op` миграции.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from app import crypto

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "b2c3d4e5f6a7_encrypt_pii_columns.py"
)
CURRENT = crypto._derive_fernet("current-pii-secret")
LEGACY = crypto._derive_fernet("old-session-secret")


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_b2c3d4e5f6a7", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeOp:
    def __init__(self, conn):
        self._conn = conn

    def get_bind(self):
        return self._conn

    def alter_column(self, *args, **kwargs):
        pass  # SQLite не меняет тип колонки, а проверяем здесь только данные


@pytest.fixture()
def keys(monkeypatch):
    monkeypatch.setattr(crypto, "_get_fernet", lambda: CURRENT)
    monkeypatch.setattr(crypto, "_get_legacy_fernet", lambda: LEGACY)


def _enc(fernet, value):
    return fernet.encrypt(value.encode()).decode()


def _run_downgrade(rows):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, phone TEXT, "
            "parent_phone TEXT, tg_username TEXT)"
        ))
        for row in rows:
            conn.execute(sa.text(
                "INSERT INTO users VALUES (:id, :phone, :parent_phone, :tg_username)"
            ), row)
        migration = _load_migration()
        migration.op = _FakeOp(conn)
        migration.downgrade()
        return {
            r.id: (r.phone, r.parent_phone, r.tg_username)
            for r in conn.execute(sa.text("SELECT * FROM users ORDER BY id"))
        }


def test_downgrade_decrypts_current_and_legacy_keys_keeps_plaintext(keys):
    result = _run_downgrade([
        {"id": 1, "phone": _enc(CURRENT, "+79990000001"),
         "parent_phone": None, "tg_username": _enc(CURRENT, "anna")},
        {"id": 2, "phone": _enc(LEGACY, "+79990000002"),
         "parent_phone": _enc(LEGACY, "+79990000003"), "tg_username": None},
        {"id": 3, "phone": "+79990000004", "parent_phone": None, "tg_username": "boris"},
    ])

    assert result == {
        1: ("+79990000001", None, "anna"),
        2: ("+79990000002", "+79990000003", None),
        3: ("+79990000004", None, "boris"),
    }


def test_downgrade_refuses_token_it_cannot_decrypt(keys):
    foreign = Fernet(Fernet.generate_key())
    token = _enc(foreign, "anna")
    assert len(token) == 100, "короткий ник в шифре влезал в VARCHAR(100)"

    with pytest.raises(RuntimeError, match="id=7"):
        _run_downgrade([
            {"id": 7, "phone": None, "parent_phone": None, "tg_username": token},
        ])
