"""Дайджест месяца на экране ученика (/cabinet/tracker) — вкладка рядом с задачами."""

from datetime import date, timedelta

from app.models.tag import Tag, UserTag
from app.services.tracker import (
    MONTH_GENITIVE,
    create_digest,
    create_event,
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


def test_digest_lives_on_its_own_tab(client, db, user_factory, session_factory):
    """Дайджест лежит во вкладке «Дайджест», задачи открыты по умолчанию.

    Решение владельца 17.09.2026 отменяет и «первый блок сверху» (22.08), и
    «открыт без клика» (16.09): дайджест переехал за вкладку.
    """
    student = user_factory(vk_id=430_012, name="Ученик", role_name="ученик")
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
    assert 'data-trk-tab="tasks"' in response.text
    assert 'data-trk-tab="digest"' in response.text
    assert 'id="trkPanelDigest"' in response.text
    # Панель задач открыта, дайджест спрятан до клика по вкладке.
    assert 'id="trkPanelTasks" aria-labelledby="trkTabTasks">' in response.text
    assert 'id="trkPanelDigest" aria-labelledby="trkTabDigest" hidden' in response.text
    assert "Общий эфир" in response.text
    # Ученик читает тему месяца, а не служебное имя дайджеста.
    assert "Композиция" in response.text
    assert "Служебное имя" not in response.text


def test_no_tab_strip_without_a_digest(client, db, user_factory, session_factory):
    """Нет дайджеста — нет и полосы вкладок: переключать нечего."""
    student = user_factory(vk_id=430_013, name="Ученик", role_name="ученик")
    session = session_factory(student)
    client.cookies.set("session_id", session.id)

    response = client.get(PAGE)
    assert response.status_code == 200
    # Ищем саму полосу, а не `data-trk-tab`: эта строка есть и в скрипте
    # переключения, он в разметке всегда и без кнопок просто ничего не делает.
    assert 'class="nav-pill trk-tabs"' not in response.text
    assert 'class="dgst"' not in response.text


def test_empty_month_says_so_instead_of_an_empty_list(client, db, user_factory, session_factory):
    """Дайджест без событий не молчит — прямо говорит, что событий нет."""
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
    assert "В этом месяце пока нет событий." in response.text


def test_event_list_shows_dates_and_kind(client, db, user_factory, session_factory):
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
        db, digest.id, kind="mock_exam", title="Окно пробника", note=None,
        starts_on=start, ends_on=start + timedelta(days=5), meeting_url=None,
    )
    publish_digest(digest, user_id=student.id)
    db.commit()

    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Окно пробника" in response.text
    assert "Пробник" in response.text
    assert f"1–6 {MONTH_GENITIVE[today.month]}" in response.text
