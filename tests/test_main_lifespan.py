"""Lifecycle guards for optional integrations."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import app.main as main_module


def test_lifespan_does_not_open_n8n_clients_when_disabled(monkeypatch):
    monkeypatch.setattr(main_module.settings, "n8n_enabled", False)
    monkeypatch.setattr(main_module.settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(main_module.settings, "database_url", "postgresql://test")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "lifespan-disabled")

    db = MagicMock()
    monkeypatch.setattr(main_module, "SessionLocal", MagicMock(return_value=db))
    monkeypatch.setattr(main_module, "seed_roles_and_permissions", MagicMock())

    n8n_init = AsyncMock()
    n8n_close = AsyncMock()
    drive_init = AsyncMock()
    drive_close = AsyncMock()
    vk_init = AsyncMock()
    vk_close = AsyncMock()
    monkeypatch.setattr(main_module.n8n_service, "init_client", n8n_init)
    monkeypatch.setattr(main_module.n8n_service, "close_client", n8n_close)
    monkeypatch.setattr(main_module.drive_service, "init_client", drive_init)
    monkeypatch.setattr(main_module.drive_service, "close_client", drive_close)
    monkeypatch.setattr(main_module.vk_service, "init_client", vk_init)
    monkeypatch.setattr(main_module.vk_service, "close_client", vk_close)

    async def run_lifespan():
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(run_lifespan())

    n8n_init.assert_not_awaited()
    n8n_close.assert_not_awaited()
    drive_init.assert_not_awaited()
    drive_close.assert_not_awaited()
    vk_init.assert_awaited_once()
    vk_close.assert_awaited_once()
    db.close.assert_called_once()
