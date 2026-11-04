"""Card rules guard the page, key files, events, and statistics together."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings
from app.constants import ANNUAL_NEWCOMER_TAG, PROGRAM_HOLIDAY
from app.csrf import generate_csrf_token
from app.models.activity_event import StudentActivityEvent
from app.models.lab_access import LabCardAccess, LabCardStudent
from app.services.lab_access import asset_owners, visible_cards
from app.services.lab_stats import (
    EVENT_LAB_CARD_OPEN, EVENT_LAB_MODEL_FAILED, EVENT_LAB_MODEL_LOADED, EVENT_LAB_PAGE_OPEN,
    model_result_stats, top_cards,
)
from app.services.tags import add_tag_to_user, get_or_create_tag


def test_student_sees_only_open_card_and_records_once(
    client, db, user_factory, session_factory, tmp_path, monkeypatch,
):
    cards = list(visible_cards().values())
    first, second = cards[0], cards[1]
    root = tmp_path / "lab"
    for card in (first, second):
        target = root / card["assets"][0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"model")
    monkeypatch.setattr(settings, "lab_assets_dir", str(root))
    asset_owners.cache_clear()
    student = user_factory()
    db.add(LabCardAccess(card_id=first["id"]))
    db.flush()
    db.add(LabCardStudent(card_id=first["id"], user_id=student.id))
    db.commit()
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    page = client.get("/3dlab")
    assert page.status_code == 200
    assert first["id"] in page.text
    assert second["id"] not in page.text
    assert db.query(StudentActivityEvent).filter(StudentActivityEvent.event_type == EVENT_LAB_PAGE_OPEN).count() == 1
    assert model_result_stats(db)["no_result_students"] == 1
    assert client.get(f"/lab/asset/{first['assets'][0]}").status_code == 200
    assert client.get(f"/lab/asset/{second['assets'][0]}").status_code == 403

    payload = {"card_id": first["id"], "csrf_token": generate_csrf_token(session.id)}
    assert client.post("/lab/event", json=payload).json() == {"recorded": True}
    assert client.post("/lab/event", json=payload).json() == {"recorded": False}
    assert client.post("/lab/event", json={**payload, "card_id": second["id"]}).status_code == 403
    assert client.post("/lab/event", json={**payload, "csrf_token": "wrong"}).status_code == 403
    rows = db.query(StudentActivityEvent).filter(StudentActivityEvent.event_type == EVENT_LAB_CARD_OPEN).all()
    assert len(rows) == 1
    assert rows[0].details == f"card:{first['id']}"
    assert top_cards(db)[0]["students"] == 1


def test_model_result_tracks_loaded_and_failed_students_without_exposing_closed_cards(
    client, db, user_factory, session_factory,
):
    first, second = list(visible_cards())[:2]
    student = user_factory()
    db.add(LabCardAccess(card_id=first))
    db.commit()
    session = session_factory(student)
    client.cookies.set("session_id", session.id)
    payload = {"card_id": first, "csrf_token": generate_csrf_token(session.id)}

    assert client.post("/lab/event", json={**payload, "model_result": "failed"}).json() == {"recorded": True}
    assert client.post("/lab/event", json={**payload, "model_result": "failed"}).json() == {"recorded": False}
    assert client.post("/lab/event", json={**payload, "model_result": "loaded"}).json() == {"recorded": True}
    assert client.post("/lab/event", json={**payload, "model_result": "unknown"}).status_code == 422
    assert client.post("/lab/event", json={**payload, "card_id": second, "model_result": "failed"}).status_code == 403

    assert db.query(StudentActivityEvent).filter(StudentActivityEvent.event_type == EVENT_LAB_MODEL_FAILED).count() == 1
    assert db.query(StudentActivityEvent).filter(StudentActivityEvent.event_type == EVENT_LAB_MODEL_LOADED).count() == 1
    stats = model_result_stats(db)
    assert (stats["attempted_students"], stats["loaded_students"], stats["failed_students"]) == (1, 1, 1)
    assert stats["failures"][0]["name"] == student.name


def test_head_teacher_can_set_card_access_and_audits_change(
    client, db, user_factory, session_factory,
):
    from app.models.audit_log import AuditLog
    head = user_factory(vk_id=800_001, role_name="админ", is_admin=True)
    session = session_factory(head)
    client.cookies.set("session_id", session.id)
    card_id = next(iter(visible_cards()))
    assert client.get("/cabinet/3dlab/figures").status_code == 200
    response = client.post(
        "/cabinet/3dlab/figures",
        json={"card_ids": [card_id], "tariffs": [head.tariff], "levels": [], "tag_ids": [], "user_ids": []},
        headers={"X-CSRF-Token": generate_csrf_token(session.id)},
    )
    assert response.status_code == 200
    assert db.get(LabCardAccess, card_id) is not None
    assert db.query(AuditLog).filter(AuditLog.action == "lab_card_access").count() == 1

    # Пустой выбор закрывает фигуру, а массовое сохранение меняет весь набор.
    second_id = list(visible_cards())[1]
    bulk = client.post(
        "/cabinet/3dlab/figures",
        json={"card_ids": [card_id, second_id], "tariffs": [], "levels": [], "tag_ids": [], "user_ids": []},
        headers={"X-CSRF-Token": generate_csrf_token(session.id)},
    )
    assert bulk.json() == {"updated": 2}
    assert db.get(LabCardAccess, card_id) is None
    assert db.get(LabCardAccess, second_id) is None
    assert db.query(AuditLog).filter(AuditLog.action == "lab_card_access").count() == 3


def test_student_cannot_edit_figures_or_record_staff_event(client, db, user_factory, session_factory):
    student = user_factory(vk_id=800_002)
    session = session_factory(student)
    client.cookies.set("session_id", session.id)
    card_id = next(iter(visible_cards()))
    assert client.get("/cabinet/3dlab/figures").status_code == 403
    assert client.post(
        "/cabinet/3dlab/figures",
        json={"card_ids": [card_id]},
        headers={"X-CSRF-Token": generate_csrf_token(session.id)},
    ).status_code == 403
    assert client.post(
        "/lab/event",
        json={"card_id": card_id, "csrf_token": generate_csrf_token(session.id)},
    ).status_code == 403
    assert db.query(StudentActivityEvent).filter(StudentActivityEvent.event_type == EVENT_LAB_CARD_OPEN).count() == 0


def test_everyone_rule_opens_visible_cards_to_new_students(client, db, user_factory, session_factory):
    from app.services.lab_access import open_card_ids

    head = user_factory(vk_id=800_003, role_name="админ", is_admin=True)
    session = session_factory(head)
    client.cookies.set("session_id", session.id)
    ids = list(visible_cards())
    token = generate_csrf_token(session.id)
    response = client.post(
        "/cabinet/3dlab/figures",
        json={"card_ids": ids, "everyone": True},
        headers={"X-CSRF-Token": token},
    )
    assert response.json() == {"updated": len(ids)}
    assert db.query(LabCardAccess).count() == len(ids)
    assert '"everyone": true' in client.get("/cabinet/3dlab/figures").text
    rejected = client.post(
        "/cabinet/3dlab/figures",
        json={"card_ids": ids, "everyone": True, "tariffs": [head.tariff]},
        headers={"X-CSRF-Token": token},
    )
    assert rejected.status_code == 422

    new_student = user_factory(vk_id=800_004)
    assert open_card_ids(db, {"role_rank": 1, "user_id": new_student.id, "tariff": new_student.tariff}) == set(ids)
    student_session = session_factory(new_student)
    client.cookies.clear()
    client.cookies.set("session_id", student_session.id)
    assert client.get("/3dlab").status_code == 200
    assert client.post(
        "/lab/event",
        json={"card_id": ids[0], "csrf_token": generate_csrf_token(student_session.id)},
    ).json() == {"recorded": True}


@pytest.mark.parametrize("scenario", ["holiday", "adaptation", "expired"])
def test_everyone_rule_reaches_all_student_courses(
    scenario, client, db, user_factory, session_factory, tmp_path, monkeypatch,
):
    card = next(iter(visible_cards().values()))
    root = tmp_path / "lab"
    target = root / card["assets"][0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"model")
    monkeypatch.setattr(settings, "lab_assets_dir", str(root))
    asset_owners.cache_clear()
    db.add(LabCardAccess(card_id=card["id"]))
    student = user_factory()
    if scenario == "holiday":
        student.program = PROGRAM_HOLIDAY
    elif scenario == "adaptation":
        student.profile_completed = True
        student.portfolio_do_completed = False
        tag = get_or_create_tag(db, ANNUAL_NEWCOMER_TAG)
        add_tag_to_user(db, student.id, tag.id)
    else:
        student.access_until = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    blocked = scenario in {"holiday", "expired"}
    assert client.get("/3dlab", follow_redirects=False).status_code == (302 if blocked else 200)
    assert client.get(f"/lab/asset/{card['assets'][0]}").status_code == (403 if blocked else 200)
    event = client.post("/lab/event", json={
        "card_id": card["id"], "csrf_token": generate_csrf_token(session.id),
    })
    assert event.status_code == (403 if blocked else 200)
    if not blocked:
        assert event.json() == {"recorded": True}
    assert ('data-nav-key="3dlab"' in client.get("/cabinet/personal").text) is not blocked
