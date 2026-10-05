"""Задания прямо на этапе видны и правятся в конструкторе (владелец 29.09.2026).

С 24.09.2026 «Портфолио» живёт на этапе «Предобучение», а не в цикле. Экран
заданий, создание и перестановка пускали только цикл, и у ГП и суперадмина
задания этапа не было видно нигде — хотя ученик его видел. Экран тот же, что
у цикла (`/cabinet/staff/program/cycles/{topic_id}`); правка и удаление самой
рамки у этапа остаются на экране «Этапы».
"""

from datetime import timedelta

from app.models.learning_topic import TOPIC_KIND_STAGE, TOPIC_KIND_WEEK, LearningTopic
from app.models.tracker import TrackerTask
from app.services.tz import today_msk

PROGRAM = "/cabinet/staff/program"
CSRF = {"X-CSRF-Token": "x"}


def _login_chief(client, user_factory, session_factory):
    chief = user_factory(vk_id=960_001, name="Главный", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(chief).id)


def _stage_with_cycle(client, db):
    today = today_msk()
    dates = {
        "description": None, "is_published": True,
        "starts_on": today.isoformat(), "ends_on": (today + timedelta(days=30)).isoformat(),
    }
    # Этап всегда внутри периода (владелец 06.10.2026).
    period = client.post(f"{PROGRAM}/periods", json={"title": "Период", **dates}, headers=CSRF)
    assert period.status_code == 200, period.text
    resp = client.post(f"{PROGRAM}/stages", json={
        "title": "Предобучение", "period_id": period.json()["period_id"], **dates,
    }, headers=CSRF)
    assert resp.status_code == 200, resp.text
    stage = db.query(LearningTopic).filter(LearningTopic.kind == TOPIC_KIND_STAGE).one()
    resp = client.post(f"{PROGRAM}/cycles", json={
        "title": "Цикл 1", "description": None,
        "starts_on": today.isoformat(), "ends_on": (today + timedelta(days=7)).isoformat(),
        "is_published": True, "stage_id": stage.id,
    }, headers=CSRF)
    assert resp.status_code == 200, resp.text
    cycle = db.query(LearningTopic).filter(LearningTopic.kind == TOPIC_KIND_WEEK).one()
    return stage, cycle


def _create_item(client, topic_id, title):
    resp = client.post(f"{PROGRAM}/cycles/{topic_id}/items/material", json={
        "title": title, "description": None, "subject": None,
        "is_required": False, "starts_on": None, "blocks": [],
    }, headers=CSRF)
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


def test_stage_task_is_listed_and_editable(client, db, user_factory, session_factory):
    _login_chief(client, user_factory, session_factory)
    stage, cycle = _stage_with_cycle(client, db)

    portfolio_id = _create_item(client, stage.id, "Портфолио")
    second_id = _create_item(client, stage.id, "Анкета")

    assert db.get(TrackerTask, portfolio_id).topic_id == stage.id
    page = client.get(f"{PROGRAM}/cycles/{stage.id}")
    assert page.status_code == 200
    assert "Портфолио" in page.text
    # С этапа — назад к этапам и плашки в его циклы.
    assert f'href="{PROGRAM}/stages"' in page.text
    assert f'href="{PROGRAM}/cycles/{cycle.id}"' in page.text

    move = client.post(
        f"{PROGRAM}/cycles/{stage.id}/items/{second_id}/move",
        json={"direction": -1}, headers=CSRF,
    )
    assert move.status_code == 200, move.text
    assert move.json()["order"] == [second_id, portfolio_id]


def test_stages_page_links_to_stage_tasks(client, db, user_factory, session_factory):
    _login_chief(client, user_factory, session_factory)
    stage, _ = _stage_with_cycle(client, db)
    _create_item(client, stage.id, "Портфолио")

    page = client.get(f"{PROGRAM}/stages").text

    assert f'href="{PROGRAM}/cycles/{stage.id}">Задания этапа (1)</a>' in page


def test_cycle_page_links_to_its_stage_tasks(client, db, user_factory, session_factory):
    _login_chief(client, user_factory, session_factory)
    stage, cycle = _stage_with_cycle(client, db)
    today = today_msk()
    client.post(f"{PROGRAM}/cycles", json={
        "title": "Цикл 2", "description": None,
        "starts_on": (today + timedelta(days=8)).isoformat(),
        "ends_on": (today + timedelta(days=14)).isoformat(),
        "is_published": True, "stage_id": stage.id,
    }, headers=CSRF)

    page = client.get(f"{PROGRAM}/cycles/{cycle.id}").text

    assert f'<a href="{PROGRAM}/cycles/{stage.id}">' in page
    assert f'href="{PROGRAM}/cycles">К циклам</a>' in page


def test_stage_frame_is_still_edited_only_on_stages_screen(
    client, db, user_factory, session_factory
):
    """Статистика и удаление — у цикла; этап через них не проходит."""
    _login_chief(client, user_factory, session_factory)
    stage, _ = _stage_with_cycle(client, db)

    assert client.get(f"{PROGRAM}/cycles/{stage.id}/stats").status_code == 404
    assert client.post(f"{PROGRAM}/cycles/{stage.id}/delete", headers=CSRF).status_code == 404
