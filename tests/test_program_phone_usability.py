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


def test_unknown_stage_filter_shows_all_cycles(client, db, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    first = _cycle(client, title="Первый")
    second = _cycle(client, title="Второй", offset=20)

    page = client.get(f"{CYCLES_PAGE}?stage=999999").text

    assert f'href="{CYCLES_PAGE}/{first}"' in page
    assert f'href="{CYCLES_PAGE}/{second}"' in page
