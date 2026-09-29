"""VK удалён из продукта (владелец 29.09.2026).

«Удаление ВК вообще из всего, чтобы нигде оно не участвовало, нигде оно не
показывалось. Оно всё не нужно, у нас только Telegram». Вход через VK,
повторная проверка группы, ссылка на ВКонтакте в анкете и «Личной
информации», «VK ID» у сотрудников — сняты.

Остаётся внутреннее: колонка `users.vk_id` (обязательная, уникальная, в путях
S3; Telegram-аккаунтам пишется служебный номер), данные `vk_profile_url` и
`last_vk_check_at` без показа, выдача ссылок через n8n (выключена с 13.07.2026,
отвечает 503). Юридические тексты (`partials/legal/`) не трогаются — решение
владельца.
"""

import importlib
import re
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from app.config import Settings
from app.main import app

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
VK_TRACES = re.compile(r"/auth/vk|vk\.com|vk\.ru|ВКонтакт|VK ID|группы ВК|во ВК\b|Вступить в сообщество", re.I)


def test_no_vk_routes():
    paths = [r.path for r in app.routes if isinstance(r, APIRoute)]
    assert not [p for p in paths if p.startswith("/auth/vk")]


def test_no_vk_service_and_settings():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.vk")
    assert not [name for name in Settings.model_fields if name.startswith("vk_")]


def test_no_vk_pkce_in_cache():
    from app import cache

    assert not hasattr(cache, "set_vk_pkce")
    assert not hasattr(cache, "pop_vk_pkce")


def test_templates_do_not_show_vk():
    found = []
    for path in TEMPLATES.rglob("*.html"):
        if "legal" in path.parts:
            continue  # юрдокументы — решение владельца, не трогаем
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if VK_TRACES.search(line):
                found.append(f"{path.relative_to(TEMPLATES)}:{number}: {line.strip()[:90]}")
    assert not found, "VK на экранах:\n" + "\n".join(found)


def test_profile_saves_without_vk_link(auth_client, db):
    """Анкета ученика больше не спрашивает ссылку на ВКонтакте."""
    client, user = auth_client
    user.profile_completed = False
    db.commit()

    resp = client.get("/cabinet/profile")

    assert resp.status_code == 200
    assert 'name="vk_profile_url"' not in resp.text


def test_personal_contacts_form_has_no_vk(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/personal/contacts")

    assert resp.status_code == 200
    assert 'name="vk_profile_url"' not in resp.text
