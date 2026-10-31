"""Card rules guard the page, key files, events, and statistics together."""
from pathlib import Path

from app.config import settings
from app.csrf import generate_csrf_token
from app.models.activity_event import StudentActivityEvent
from app.models.lab_access import LabCardAccess, LabCardStudent
from app.services.lab_access import asset_owners, visible_cards
from app.services.lab_stats import EVENT_LAB_CARD_OPEN, top_cards


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
