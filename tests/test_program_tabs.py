"""Раздел «Учебные программы»: одна шапка вкладок на четырёх экранах.

Дайджест, Цели и Видео перестали быть пунктами меню персонала и стали
вкладками этого раздела. Тест держит контракт: шапка есть на всех четырёх
страницах, активна ровно одна вкладка, и из меню эти пункты ушли.
"""

import pytest

from app.services.navigation import staff_nav_items

PAGES = (
    "/cabinet/staff/program",
    "/cabinet/staff/digest",
    "/cabinet/staff/goals",
    "/cabinet/admin/videos",
)


def _staff_client(client, user_factory, session_factory, *, vk_id=430_001):
    user = user_factory(
        vk_id=vk_id,
        name="Главный преподаватель",
        is_admin=True,
        is_group_member=False,
        role_name="админ",
    )
    session = session_factory(user)
    client.cookies.set("session_id", session.id)
    return user


@pytest.mark.parametrize("page", PAGES)
def test_program_tabs_render_on_every_section_page(
    page, client, user_factory, session_factory
):
    _staff_client(client, user_factory, session_factory)
    response = client.get(page)

    assert response.status_code == 200
    assert 'aria-label="Разделы учебных программ"' in response.text
    for tab in ("Календарь", "Дайджест", "Цели", "Видео"):
        assert f">{tab}</a>" in response.text
    # Ровно одна вкладка подсвечена — иначе неясно, где ты находишься.
    assert response.text.count("prg-tab is-active") == 1


def test_moved_sections_are_gone_from_staff_menu():
    for rank in (4, 5):
        keys = [item.key for item in staff_nav_items(role_rank=rank)]
        assert "tracker" not in keys
        assert "digest" not in keys
        assert "goals" not in keys
        assert "videos" not in keys
        assert "program" in keys
