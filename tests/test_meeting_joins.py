"""Нажатия «Подключиться» у событий дайджеста — в статистике карточки ученика
(владелец 20.10.2026, `services/meeting_joins.py`)."""

from app.models.activity_event import StudentActivityEvent
from app.models.session import Session as DbSession
from app.services.activity_stats import get_student_event_stats, student_activity
from app.services.meeting_joins import EVENT_MEETING_JOIN
from app.services.schedule_event_types import create_type
from app.services.tracker import (
    create_digest,
    create_event,
    delete_event,
    publish_digest,
    set_event_tariffs,
)
from app.services.tz import today_msk

URL = "/cabinet/tracker/digest/events/{}/join"
MEET = "https://telemost.yandex.ru/j/123"


def _event(db, author_id, *, title="Занятие по рисунку", meeting_url=MEET,
           tariffs=(), published=True):
    today = today_msk()
    digest = create_digest(
        db, title="Октябрь", year=today.year, month=today.month,
        assign_to_all=True, user_id=author_id,
    )
    event = create_event(
        db, digest.id, type_id=create_type(db, name=f"Тип {title}", color="teal", style="fill").id,
        title=title, note=None, starts_on=today, ends_on=today, meeting_url=meeting_url,
    )
    if tariffs:
        set_event_tariffs(db, event, list(tariffs))
    if published:
        publish_digest(digest, user_id=author_id)
    db.commit()
    return event


def _joins(db, user_id):
    return (
        db.query(StudentActivityEvent)
        .filter(
            StudentActivityEvent.user_id == user_id,
            StudentActivityEvent.event_type == EVENT_MEETING_JOIN,
        )
        .all()
    )


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def test_click_is_recorded_once_per_event(client, db, user_factory, session_factory):
    student = user_factory(vk_id=470_001, name="Ученик", role_name="ученик")
    _login(client, session_factory, student)
    event = _event(db, student.id)

    first = client.post(URL.format(event.id))
    second = client.post(URL.format(event.id))

    assert first.status_code == 200 and first.json()["recorded"] is True
    assert second.status_code == 200 and second.json()["recorded"] is False
    assert len(_joins(db, student.id)) == 1


def test_digest_list_and_day_popup_mark_join_buttons(client, db, user_factory, session_factory):
    student = user_factory(vk_id=470_002, name="Ученик", role_name="ученик")
    _login(client, session_factory, student)
    event = _event(db, student.id)

    page = client.get("/cabinet/tracker").text

    assert f'data-meeting-join="{event.id}"' in page
    assert "/static/js/meeting-join.js" in page
    # Окну дня номер события нужен, чтобы пометить кнопку, собранную на лету.
    assert f'"id": {event.id}' in page


def test_event_of_other_tariff_is_not_recorded(client, db, user_factory, session_factory):
    student = user_factory(vk_id=470_003, name="Ученик", role_name="ученик", tariff="Я САМ")
    _login(client, session_factory, student)
    event = _event(db, student.id, tariffs=["Я С ВАМИ"])

    assert client.post(URL.format(event.id)).status_code == 404
    assert _joins(db, student.id) == []


def test_unpublished_digest_and_event_without_link_are_not_recorded(
    client, db, user_factory, session_factory,
):
    student = user_factory(vk_id=470_004, name="Ученик", role_name="ученик")
    _login(client, session_factory, student)
    draft = _event(db, student.id, title="Черновик", published=False)
    no_link = _event(db, student.id, title="Без ссылки", meeting_url=None)

    assert client.post(URL.format(draft.id)).status_code == 404
    assert client.post(URL.format(no_link.id)).status_code == 404
    assert _joins(db, student.id) == []


def test_impersonation_is_not_recorded(client, db, user_factory, session_factory):
    """Сотрудник «глазами» ученика нажал кнопку — подключения ученика нет."""
    student = user_factory(vk_id=470_005, name="Ученик", role_name="ученик")
    admin = user_factory(vk_id=470_006, name="Админ", role_name="суперадмин", is_admin=True)
    session = session_factory(student)
    session.impersonated_by_id = admin.id
    db.commit()
    client.cookies.set("session_id", session.id)
    event = _event(db, admin.id)

    response = client.post(URL.format(event.id))

    assert response.status_code == 200 and response.json()["recorded"] is False
    assert _joins(db, student.id) == []


def test_card_statistics_lists_joins_and_feed_names_event(db, user_factory):
    student = user_factory(vk_id=470_007, name="Ученик", role_name="ученик")
    event = _event(db, student.id, title="Разбор композиции")
    db.add(StudentActivityEvent(
        user_id=student.id, event_type=EVENT_MEETING_JOIN, details=f"event:{event.id}",
    ))
    db.commit()

    data = student_activity(db, student, today=today_msk())

    assert data["meetings"]["recent"] == 1
    [join] = data["meetings"]["joins"]
    assert join["event"].startswith("Разбор композиции · ")
    assert join["joined_at"]
    [item] = data["feed"]
    assert item["label"] == "Нажал «Подключиться»"
    assert item["details"].startswith("Разбор композиции · ")


def test_deleted_event_keeps_join_with_placeholder(db, user_factory):
    student = user_factory(vk_id=470_008, name="Ученик", role_name="ученик")
    event = _event(db, student.id)
    db.add(StudentActivityEvent(
        user_id=student.id, event_type=EVENT_MEETING_JOIN, details=f"event:{event.id}",
    ))
    delete_event(db, event)
    db.commit()

    [join] = student_activity(db, student, today=today_msk())["meetings"]["joins"]

    assert join["event"] == "событие удалено из дайджеста"


def test_school_summary_does_not_count_join_as_upload(db, user_factory):
    """До 20.10.2026 сводка считала загрузкой всё, что не вход."""
    student = user_factory(vk_id=470_009, name="Ученик", role_name="ученик")
    db.add(StudentActivityEvent(user_id=student.id, event_type=EVENT_MEETING_JOIN, details="event:1"))
    db.commit()

    stats = get_student_event_stats(db)

    [row] = [r for r in stats["top"] if r["student_name"]]
    assert row["uploads"] == 0
    [by_type] = stats["by_type"]
    assert by_type["label"] == "Нажатия «Подключиться» у занятий"
