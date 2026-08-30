"""Пробник внутри дня учебной программы."""

from datetime import date, datetime, timedelta, timezone

from app.constants import FEATURE_MOCK_EXAM
from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketTag
from app.models.feature_period import FeaturePeriod
from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, LearningTopic
from app.models.tag import Tag, UserTag
from app.models.tracker import TrackerTask
from app.services.exam_cycle import get_active_tickets

PROGRAM = "/cabinet/staff/program"
TODAY = date(2026, 8, 21)
MONDAY = "2026-08-24"


def _staff_client(client, user_factory, session_factory, *, vk_id=530_004):
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


def _freeze(monkeypatch, value: date = TODAY):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.program.today_msk", lambda: value)
    monkeypatch.setattr("app.services.exam_tickets.today_msk", lambda: value)


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def _ticket(title="Натюрморт"):
    # Окно билета (opens_at/closes_at/duration_minutes) больше не приходит с
    # клиента (решение владельца 30.08.2026) — сервер сам берёт
    # `default_schedule_for_day(day)` по дню из URL.
    return {
        "title": title,
        "description": "Два листа",
    }


def _payload(subjects, *, tag_ids=None, assign_to_all=False, usernames=""):
    return {
        "subjects": subjects,
        "audience": {
            "assign_to_all": assign_to_all,
            "tag_ids": tag_ids or [],
            "assignee_usernames": usernames,
        },
    }


# ── Создание ──────────────────────────────────────────────────────────────

def test_two_subjects_become_two_assignments(
    client, db, user_factory, session_factory, monkeypatch
):
    """Предмет у задания один и NOT NULL, поэтому два предмета — два задания."""
    _freeze(monkeypatch)
    admin = _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload(
            [
                {"subject": "Рисунок", "tickets": [_ticket("Натюрморт")]},
                {"subject": "Композиция", "tickets": [_ticket("Композиция с аркой")]},
            ],
            tag_ids=[tag.id],
        ),
    )

    assert response.status_code == 200
    assignments = db.query(ExamAssignment).order_by(ExamAssignment.id).all()
    assert [a.subject for a in assignments] == ["Рисунок", "Композиция"]
    assert all(a.kind == "mock" and a.status == "published" for a in assignments)
    assert all(a.created_by_id == admin.id for a in assignments)
    assert db.query(ExamTicket).count() == 2
    # По элементу программы на предмет, оба видны в дне.
    tasks = db.query(TrackerTask).all()
    assert {t.subject for t in tasks} == {"Рисунок", "Композиция"}
    assert all(t.kind == "mock_exam" and t.is_published for t in tasks)
    assert all(t.source_kind == "exam_assignment" for t in tasks)


def test_one_subject_is_enough(client, db, user_factory, session_factory, monkeypatch):
    """Владелец добавляет рисунок и пропускает композицию."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload([{"subject": "Рисунок", "tickets": [_ticket()]}], tag_ids=[tag.id]),
    )

    assert response.status_code == 200
    assert db.query(ExamAssignment).count() == 1


def test_item_appears_in_the_day_and_in_the_calendar(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload([{"subject": "Рисунок", "tickets": [_ticket()]}], tag_ids=[tag.id]),
    )

    day_page = client.get(f"{PROGRAM}/{MONDAY}").text
    assert "Пробник по предмету «Рисунок»" in day_page
    month_page = client.get(f"{PROGRAM}?month=2026-08").text
    assert 'title="Пробник: Рисунок"' in month_page


def test_service_topic_carries_the_audience(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    student = user_factory(vk_id=531_001, role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()

    response = client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload([{"subject": "Рисунок", "tickets": [_ticket()]}], tag_ids=[tag.id]),
    )

    assert response.json()["audience_size"] == 1
    topic = db.query(LearningTopic).filter_by(kind=TOPIC_KIND_PROGRAM_ITEM).one()
    assert topic.is_published is True
    # Тема открывается в понедельник своей недели: ученик видит неделю целиком.
    assert topic.opens_at.astimezone(timezone.utc).date() <= date(2026, 8, 24)


def test_all_selected_tags_reach_the_ticket(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tariff = _tag(db, "МАКСИМУМ")
    extra = _tag(db, "Поток 1")
    student = user_factory(vk_id=531_002, role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=extra.id))
    db.commit()

    client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload(
            [{"subject": "Рисунок", "tickets": [_ticket()]}], tag_ids=[tariff.id, extra.id]
        ),
    )

    ticket = db.query(ExamTicket).one()
    assert ticket.target_tag_id == tariff.id
    assert {row.tag_id for row in db.query(ExamTicketTag).all()} == {tariff.id, extra.id}


def test_ticket_is_closed_until_its_window_opens(
    client, db, user_factory, session_factory, monkeypatch
):
    """Пробник на следующую неделю не должен всплыть у ученика сегодня."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    student = user_factory(vk_id=531_003, role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()
    future = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()

    client.post(
        f"{PROGRAM}/{future}/mock",
        json=_payload(
            [{"subject": "Рисунок", "tickets": [_ticket()]}], tag_ids=[tag.id]
        ),
    )

    assert get_active_tickets(db, student.id, "Рисунок") == []


def test_period_starts_on_the_exam_day(
    client, db, user_factory, session_factory, monkeypatch
):
    """Старая форма открывала период с сегодня — это открывало старые билеты."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload([{"subject": "Рисунок", "tickets": [_ticket()]}], tag_ids=[tag.id]),
    )

    period = db.query(FeaturePeriod).filter_by(feature=FEATURE_MOCK_EXAM).one()
    assert period.start_date == date(2026, 8, 24)
    assert period.end_date == date(2026, 8, 24)


# ── Отказы ────────────────────────────────────────────────────────────────

def test_empty_audience_is_rejected(client, user_factory, session_factory, monkeypatch):
    """Иначе повторилась бы ловушка старой формы: билет уезжает всей школе."""
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload([{"subject": "Рисунок", "tickets": [_ticket()]}]),
    )

    assert response.status_code == 422
    assert "кому" in response.json()["detail"].lower()


def test_all_students_option_needs_no_tags(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    user_factory(vk_id=532_001, role_name="ученик")

    response = client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload([{"subject": "Рисунок", "tickets": [_ticket()]}], assign_to_all=True),
    )

    assert response.status_code == 200
    assert db.query(ExamTicket).one().assign_to_all is True


def test_past_day_refuses_new_mock(client, db, user_factory, session_factory, monkeypatch):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/2026-08-17/mock",
        json=_payload(
            [{"subject": "Рисунок", "tickets": [_ticket()]}],
            tag_ids=[tag.id],
        ),
    )

    assert response.status_code == 422


def test_same_subject_twice_is_rejected(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")

    response = client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload(
            [
                {"subject": "Рисунок", "tickets": [_ticket("Первый")]},
                {"subject": "Рисунок", "tickets": [_ticket("Второй")]},
            ],
            tag_ids=[tag.id],
        ),
    )

    assert response.status_code == 422


def test_window_shorter_than_work_time_is_rejected(
    client, db, user_factory, session_factory, monkeypatch
):
    _freeze(monkeypatch)
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "МАКСИМУМ")
    ticket = _ticket()
    ticket["closes_at"] = f"{MONDAY}T12:00"

    response = client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload([{"subject": "Рисунок", "tickets": [ticket]}], tag_ids=[tag.id]),
    )

    assert response.status_code == 422


def test_student_cannot_create_a_mock(auth_client):
    client, _ = auth_client
    response = client.post(
        f"{PROGRAM}/{MONDAY}/mock",
        json=_payload([{"subject": "Рисунок", "tickets": [_ticket()]}], assign_to_all=True),
    )
    assert response.status_code == 403
