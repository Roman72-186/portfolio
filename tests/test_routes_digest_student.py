"""Дайджест месяца на экране ученика (/cabinet/tracker) — вкладка рядом с задачами."""

from datetime import date, timedelta

from app.models.tag import Tag, UserTag
from app.services.tracker import (
    create_digest,
    create_event,
    digest_heading,
    publish_digest,
    set_digest_tags,
)
from app.services.schedule_event_types import create_type
from app.services.tz import today_msk

PAGE = "/cabinet/tracker"


def _type(db, name="Пробник", color="teal", style="ring"):
    """Тип события: от него у события и подпись, и цвет (04.10.2026)."""
    return create_type(db, name=name, color=color, style=style)


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
        db, digest.id, type_id=_type(db).id, title="Окно пробника", note=None,
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


def test_event_list_shows_dates_and_kind(client, db, user_factory, session_factory, rendered):
    """В списке видны дата диапазоном и тип события — легенды календаря больше нет."""
    student = user_factory(vk_id=430_015, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    today = today_msk()
    digest = create_digest(
        db, title="Сентябрь", year=today.year, month=today.month,
        assign_to_all=True, user_id=student.id, theme="Объём",
    )
    start = date(today.year, today.month, 1)
    create_event(
        db, digest.id, type_id=_type(db).id, title="Окно пробника", note=None,
        starts_on=start, ends_on=start + timedelta(days=5), meeting_url=None,
    )
    publish_digest(digest, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    [event] = rendered("cabinet_tracker.html")["digest_events"]
    assert event.title == "Окно пробника"
    assert event.type.name == "Пробник"
    assert (event.starts_on, event.ends_on) == (start, start + timedelta(days=5))


# ── Календарь, цвет и тарифы события (созвон 30.09.2026, владелец 01.10.2026) ──


def _digest_with_events(db, author_id, events):
    """events: [(title, color, tariffs)] — все на сегодня."""
    from app.services.tracker import set_event_tariffs

    today = today_msk()
    digest = create_digest(
        db, title="Октябрь", year=today.year, month=today.month,
        assign_to_all=True, user_id=author_id, theme="Старт курса",
    )
    for title, color, tariffs in events:
        event = create_event(
            db, digest.id, type_id=_type(db, f"Тип {color}", color, "fill").id, title=title,
            note=None, starts_on=today, ends_on=today, meeting_url=None,
        )
        set_event_tariffs(db, event, tariffs)
    publish_digest(digest, user_id=author_id)
    db.commit()
    return digest


def test_student_sees_only_common_events_and_events_of_own_tariff(
    client, db, user_factory, session_factory,
):
    student = user_factory(vk_id=430_021, name="Ученик", role_name="ученик", tariff="Я САМ")
    client.cookies.set("session_id", session_factory(student).id)
    _digest_with_events(db, student.id, [
        ("Общий созвон", "violet", []),
        ("Разбор для Я сам", "sky", ["Я САМ"]),
        ("Разбор для Я с вами", "pink", ["Я С ВАМИ"]),
    ])

    response = client.get(PAGE)

    assert "Общий созвон" in response.text
    assert "Разбор для Я сам" in response.text
    assert "Разбор для Я с вами" not in response.text
    # Метка чужого события не попадает и в сетку.
    assert "dgst-color--pink" not in response.text


def test_student_without_tariff_sees_only_common_events(client, db, user_factory, session_factory):
    student = user_factory(vk_id=430_022, name="Ученик", role_name="ученик", tariff=None)
    client.cookies.set("session_id", session_factory(student).id)
    _digest_with_events(db, student.id, [
        ("Общий созвон", "violet", []),
        ("Только максимум", "yellow", ["УВЕРЕННЫЙ МАКСИМУМ"]),
    ])

    response = client.get(PAGE)

    assert "Общий созвон" in response.text
    assert "Только максимум" not in response.text


def test_staff_sees_every_event_whatever_the_tariff(client, db, user_factory, session_factory):
    """Сотрудник проверяет расписание целиком — тарифа у него нет."""
    curator = user_factory(vk_id=430_023, name="Куратор", role_name="куратор", tariff=None)
    client.cookies.set("session_id", session_factory(curator).id)
    _digest_with_events(db, curator.id, [
        ("Разбор для Я сам", "sky", ["Я САМ"]),
        ("Разбор для Я с вами", "pink", ["Я С ВАМИ"]),
    ])

    response = client.get(PAGE)

    assert response.status_code == 200
    assert "Разбор для Я сам" in response.text
    assert "Разбор для Я с вами" in response.text
