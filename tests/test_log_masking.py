"""Секреты из адреса не попадают в журнал запросов uvicorn (код-ревью
28.09.2026, P2 № 15). `TestClient` через журнал uvicorn не ходит, поэтому
запись собирается руками — в том же виде, в каком её пишет uvicorn."""

import logging


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5678", "GET", path, "1.1", 302), None,
    )


def test_admin_key_and_login_token_are_masked():
    from app.log_masking import SecretQueryFilter

    masked = SecretQueryFilter()
    admin = _access_record("/auth/admin-access?key=s3cr3t-forever")
    link = _access_record("/auth/link?next=%2Fcabinet&token=abc123&x=1")
    assert masked.filter(admin) and masked.filter(link)

    assert "s3cr3t" not in admin.getMessage()
    assert "/auth/admin-access?key=***" in admin.getMessage()
    assert link.getMessage().endswith('"GET /auth/link?next=%2Fcabinet&token=***&x=1 HTTP/1.1" 302')


def test_ordinary_query_is_untouched():
    from app.log_masking import SecretQueryFilter

    record = _access_record("/cabinet/students?q=monkey&tag=1")
    SecretQueryFilter().filter(record)
    assert "/cabinet/students?q=monkey&tag=1" in record.getMessage()


def test_filter_is_installed_on_uvicorn_access_logger():
    from app.log_masking import SecretQueryFilter
    from app.main import app  # noqa: F401 — установка идёт при импорте

    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, SecretQueryFilter) for f in access.filters) == 1
