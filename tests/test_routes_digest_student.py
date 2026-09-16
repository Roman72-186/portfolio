"""Дайджест месяца на экране ученика (/cabinet/tracker) — первый блок сверху."""

from datetime import date

from app.models.tag import Tag, UserTag
from app.services.tracker import (
    create_digest,
    create_event,
    digest_calendar,
    digest_heading,
    publish_digest,
    set_digest_tags,
)
from app.services.tz import today_msk

PAGE = "/cabinet/tracker"


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def test_student_sees_published_digest_addressed_to_them(client, db, user_factory, session_factory):
    student = user_factory(vk_id=430_001, name="Ученик", role_name="ученик")
    tag = _tag(db, "Поток 1")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    digest = create_digest(
        db, title="Дайджест месяца", year=today.year, month=today.month,
        assign_to_all=False, user_id=student.id,
    )
    set_digest_tags(db, digest, [tag.id])
    create_event(
        db, digest.id, kind="mock_exam", title="Окно пробника", note=None,
        starts_on=today, ends_on=today, meeting_url=None,
    )
    publish_digest(digest, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Дайджест месяца" in response.text
    assert "Окно пробника" in response.text


def test_student_does_not_see_unpublished_digest(client, db, user_factory, session_factory):
    student = user_factory(vk_id=430_002, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    digest = create_digest(
        db, title="Черновик месяца", year=today.year, month=today.month,
        assign_to_all=True, user_id=student.id,
    )
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Черновик месяца" not in response.text


def test_student_does_not_see_digest_for_other_tag(client, db, user_factory, session_factory):
    student = user_factory(vk_id=430_003, name="Ученик", role_name="ученик")
    other_tag = _tag(db, "Поток 2")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    digest = create_digest(
        db, title="Дайджест другого потока", year=today.year, month=today.month,
        assign_to_all=False, user_id=student.id,
    )
    set_digest_tags(db, digest, [other_tag.id])
    publish_digest(digest, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Дайджест другого потока" not in response.text


def test_digest_heading_joins_month_and_theme(db, user_factory):
    """Заголовок ученику — «Сентябрь · Тема» (решение владельца 16.09.2026)."""
    staff = user_factory(vk_id=430_010, name="Препод", role_name="ученик")
    digest = create_digest(
        db, title="Сентябрь — топ-тариф", year=2026, month=9,
        assign_to_all=True, user_id=staff.id, theme="Композиция и объём",
    )
    db.commit()

    assert digest_heading(digest) == "Сентябрь · Композиция и объём"


def test_digest_heading_falls_back_to_title_without_theme(db, user_factory):
    """У дайджестов до 16.09.2026 темы нет — показываем прежнее название.

    Склеивать месяц с `title` нельзя: в прод-названиях месяц уже вписан
    руками («Сентябрь — топ-тариф»), вышло бы «Сентябрь · Сентябрь — топ-тариф».
    """
    staff = user_factory(vk_id=430_011, name="Препод", role_name="ученик")
    digest = create_digest(
        db, title="Сентябрь — топ-тариф", year=2026, month=9,
        assign_to_all=True, user_id=staff.id,
    )
    db.commit()

    assert digest_heading(digest) == "Сентябрь — топ-тариф"


def test_calendar_paints_every_day_of_a_range_event(db, user_factory):
    """«Пробник с 25 по 30» закрашивает все шесть дней, а не только первый."""
    staff = user_factory(vk_id=430_012, name="Препод", role_name="ученик")
    digest = create_digest(
        db, title="Сентябрь", year=2026, month=9,
        assign_to_all=True, user_id=staff.id, theme="Объём",
    )
    event = create_event(
        db, digest.id, kind="mock_exam", title="Окно пробника", note=None,
        starts_on=date(2026, 9, 25), ends_on=date(2026, 9, 30), meeting_url=None,
    )
    db.commit()

    days = digest_calendar(digest, [event], today=date(2026, 9, 1))
    painted = [day["number"] for day in days if day["events"]]
    assert painted == [25, 26, 27, 28, 29, 30]


def test_calendar_keeps_neighbour_month_cells_empty(db, user_factory):
    """У дней чужого месяца в сетке событий нет: у соседнего месяца свой дайджест."""
    staff = user_factory(vk_id=430_013, name="Препод", role_name="ученик")
    digest = create_digest(
        db, title="Октябрь", year=2026, month=10,
        assign_to_all=True, user_id=staff.id, theme="Свет",
    )
    # 30 сентября попадает в сетку октября: месяц начинается с четверга.
    event = create_event(
        db, digest.id, kind="deadline", title="Сдача работ", note=None,
        starts_on=date(2026, 9, 30), ends_on=date(2026, 9, 30), meeting_url=None,
    )
    db.commit()

    days = digest_calendar(digest, [event], today=date(2026, 10, 1))
    outside = [day for day in days if not day["in_month"]]
    assert outside, "в сетке октября должны быть дни соседних месяцев"
    assert all(not day["events"] for day in outside)


def test_empty_month_renders_calendar_without_events(client, db, user_factory, session_factory):
    """Дайджест без событий показывает календарь и говорит, что событий нет."""
    student = user_factory(vk_id=430_014, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    digest = create_digest(
        db, title="Пустой месяц", year=today.year, month=today.month,
        assign_to_all=True, user_id=student.id, theme="Разбор ошибок",
    )
    publish_digest(digest, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Календарь месяца" in response.text
    assert "В этом месяце пока нет событий." in response.text


def test_student_sees_calendar_open_without_a_click(client, db, user_factory, session_factory):
    """Календарь открыт сразу: расписание, на которое опираются, за клик не прячут."""
    student = user_factory(vk_id=430_015, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    digest = create_digest(
        db, title="Служебное имя", year=today.year, month=today.month,
        assign_to_all=True, user_id=student.id, theme="Композиция",
    )
    create_event(
        db, digest.id, kind="broadcast", title="Общий эфир", note=None,
        starts_on=today, ends_on=today, meeting_url=None,
    )
    publish_digest(digest, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    assert "<details" not in response.text.split('class="dgst"')[1][:2000]
    assert "Общий эфир" in response.text
    # Ученик читает тему месяца, а не служебное имя дайджеста.
    assert "Композиция" in response.text
    assert "Служебное имя" not in response.text


def test_day_with_two_events_keeps_both(db, user_factory):
    """Два события в одном дне не вытесняют друг друга: в клетке обе метки.

    Шаблон схлопывает повторы одного типа (в клетке телефона больше не
    помещается), но разные типы показывает оба.
    """
    staff = user_factory(vk_id=430_016, name="Препод", role_name="ученик")
    digest = create_digest(
        db, title="Сентябрь", year=2026, month=9,
        assign_to_all=True, user_id=staff.id, theme="Объём",
    )
    lesson = create_event(
        db, digest.id, kind="lesson", title="Занятие по рисунку", note=None,
        starts_on=date(2026, 9, 11), ends_on=date(2026, 9, 11), meeting_url=None,
    )
    deadline = create_event(
        db, digest.id, kind="deadline", title="Сдача композиции", note=None,
        starts_on=date(2026, 9, 11), ends_on=date(2026, 9, 11), meeting_url=None,
    )
    db.commit()

    days = digest_calendar(digest, [lesson, deadline], today=date(2026, 9, 1))
    day = next(d for d in days if d["number"] == 11 and d["in_month"])
    assert {e.kind for e in day["events"]} == {"lesson", "deadline"}
