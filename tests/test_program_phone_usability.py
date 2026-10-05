"""Раздел «Актуальное образовательное пространство» с телефона (аудит 29.09.2026).

Сторожа тех правок аудита, которые видно без браузера:

- «Убрать» у блока конструктора не удаляет насовсем, а оставляет «Вернуть» —
  на обоих экранах конструктора (день и задания цикла) через одну общую
  функцию, а не две копии;
- стрелки ↑↓ у заданий цикла не перезагружают страницу, поэтому сервер
  возвращает порядок из базы, и экран выстраивается по нему;
- «Циклы этапа» ведёт на список циклов именно этого этапа;
- форма нового цикла свёрнута, когда есть список, и открыта, когда его нет.
"""

import re
from datetime import timedelta

from app.models.learning_topic import TOPIC_KIND_STAGE, LearningTopic
from app.services.tz import today_msk

CYCLES_PAGE = "/cabinet/staff/program/cycles"
STAGES_PAGE = "/cabinet/staff/program/stages"
CSRF = {"X-CSRF-Token": "x"}


def _staff(client, user_factory, session_factory, *, vk_id=880_101):
    admin = user_factory(vk_id=vk_id, name="Главный", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(admin).id)
    return admin


def _stage(client, db, *, title="Предобучение"):
    dates = {
        "description": None,
        "starts_on": (today_msk() + timedelta(days=1)).isoformat(),
        "ends_on": (today_msk() + timedelta(days=60)).isoformat(),
        "is_published": True,
    }
    # Этап всегда внутри периода (владелец 06.10.2026).
    period = client.post(
        "/cabinet/staff/program/periods", json={"title": "1 семестр", **dates}, headers=CSRF,
    )
    assert period.status_code == 200, period.text
    resp = client.post(
        STAGES_PAGE,
        json={"title": title, "period_id": period.json()["period_id"], **dates},
        headers=CSRF,
    )
    assert resp.status_code == 200, resp.text
    return (
        db.query(LearningTopic)
        .filter(LearningTopic.kind == TOPIC_KIND_STAGE, LearningTopic.title == title)
        .one()
    )


def _cycle(client, *, title, offset=1, stage_id=None):
    resp = client.post(
        CYCLES_PAGE,
        json={
            "title": title,
            "description": None,
            "starts_on": (today_msk() + timedelta(days=offset)).isoformat(),
            "ends_on": (today_msk() + timedelta(days=offset + 6)).isoformat(),
            "is_published": True,
            "stage_id": stage_id,
        },
        headers=CSRF,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["cycle_id"]


def _task(client, cycle_id, title):
    resp = client.post(
        f"{CYCLES_PAGE}/{cycle_id}/items/material",
        json={
            "title": title, "description": None, "subject": None,
            "is_required": False, "starts_on": None, "blocks": [],
        },
        headers=CSRF,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


# ── «Убрать» блока оставляет «Вернуть» ─────────────────────────────────────

def test_block_remove_keeps_undo_on_both_constructor_screens(
    client, user_factory, session_factory
):
    _staff(client, user_factory, session_factory)
    cycle_id = _cycle(client, title="Цикл")
    day_iso = (today_msk() + timedelta(days=14)).isoformat()

    for url in (f"{CYCLES_PAGE}/{cycle_id}", f"/cabinet/staff/program/{day_iso}"):
        page = client.get(url)
        assert page.status_code == 200, url
        # Общая функция объявлена один раз — в партиале конструктора.
        assert page.text.count("function removeBlockRow(") == 1, url
        assert page.text.count("function moveBlockRow(") == 1, url
        assert "'Вернуть'" in page.text, url
        # Обработчик зовёт её, а не сносит строку сам, как до 29.09.2026.
        assert "removeBlockRow(removeBlock.closest('[data-block]'))" in page.text, url
        assert ".closest('[data-block]').remove()" not in page.text, url


# ── перестановка заданий без перезагрузки ──────────────────────────────────

def test_cycle_item_move_returns_order_from_db(client, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    cycle_id = _cycle(client, title="Цикл")
    first = _task(client, cycle_id, "Первое")
    second = _task(client, cycle_id, "Второе")

    move = client.post(
        f"{CYCLES_PAGE}/{cycle_id}/items/{second}/move",
        json={"direction": -1},
        headers=CSRF,
    )

    assert move.status_code == 200, move.text
    assert move.json()["order"] == [second, first]

    # Край списка — тихий no-op, порядок прежний.
    edge = client.post(
        f"{CYCLES_PAGE}/{cycle_id}/items/{second}/move",
        json={"direction": -1},
        headers=CSRF,
    )
    assert edge.json()["order"] == [second, first]


def test_cycle_items_page_moves_without_reload(client, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    cycle_id = _cycle(client, title="Цикл")
    page = client.get(f"{CYCLES_PAGE}/{cycle_id}").text

    handler = page[page.index("document.querySelectorAll('[data-item-move]')"):]
    handler = handler[:handler.index("form.addEventListener('click'")]
    success = handler[handler.index(".then(function (data)"):handler.index(".catch(")]
    assert "data.order" in success
    assert "location.reload" not in success


# ── «Циклы этапа» — циклы именно этого этапа ───────────────────────────────

def test_stage_button_opens_only_its_cycles(client, db, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    stage = _stage(client, db)
    inside = _cycle(client, title="Внутри этапа", stage_id=stage.id)
    outside = _cycle(client, title="Без этапа", offset=20)

    stages_page = client.get(STAGES_PAGE).text
    assert f'href="{CYCLES_PAGE}?stage={stage.id}"' in stages_page

    filtered = client.get(f"{CYCLES_PAGE}?stage={stage.id}").text
    assert f'href="{CYCLES_PAGE}/{inside}"' in filtered
    assert f'href="{CYCLES_PAGE}/{outside}"' not in filtered
    # Путь сверху вместо «Показать все циклы» (владелец 06.10.2026).
    assert "data-program-path" in filtered
    # Новый цикл по умолчанию заводится в тот же этап.
    assert re.search(rf'<option value="{stage.id}" selected>', filtered)


def test_unknown_stage_filter_shows_all_cycles(client, db, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    first = _cycle(client, title="Первый")
    second = _cycle(client, title="Второй", offset=20)

    page = client.get(f"{CYCLES_PAGE}?stage=999999").text

    assert f'href="{CYCLES_PAGE}/{first}"' in page
    assert f'href="{CYCLES_PAGE}/{second}"' in page
    assert "data-program-path" not in page


def test_stage_cycle_count_is_declined(client, db, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    stage = _stage(client, db)
    _cycle(client, title="Раз", stage_id=stage.id)
    _cycle(client, title="Два", offset=10, stage_id=stage.id)

    page = client.get(STAGES_PAGE).text

    assert "2 цикла внутри" in page
    assert "2 циклов" not in page


# ── форма нового цикла не заслоняет список ─────────────────────────────────

def test_new_cycle_form_collapses_once_cycles_exist(client, user_factory, session_factory):
    _staff(client, user_factory, session_factory)

    empty = client.get(CYCLES_PAGE).text
    assert "data-cycle-form>" in empty  # циклов нет — форма открыта сразу

    _cycle(client, title="Первый")
    listed = client.get(CYCLES_PAGE).text
    assert "data-cycle-form hidden>" in listed
    assert "data-cycle-new>Новый цикл</button>" in listed
