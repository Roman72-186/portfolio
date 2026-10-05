"""Дайджест: время события и края соседних месяцев (владелец 05.10.2026).

Время — два необязательных поля «с» и «до»; до этого его вписывали в
название («тренировка РИСУНОК 10:00-11:30»). Края — события соседних
месяцев на днях сетки видны в обоих дайджестах: 1 ноября из октябрьского
дайджеста раньше не попадало в ноябрьский.
"""

import json
import re
from datetime import date, time

from app.models.tracker import ScheduleEvent
from app.services.schedule_event_types import create_type
from app.services.tracker import (
    create_digest,
    create_event,
    digest_span,
    format_event_time,
    publish_digest,
    set_event_tariffs,
    shift_month,
)
from app.services.tz import today_msk

PAGE = "/cabinet/staff/digest"


def _staff(client, user_factory, session_factory):
    user = user_factory(
        vk_id=440_001, name="Главный преподаватель", is_admin=True,
        is_group_member=False, role_name="админ",
    )
    client.cookies.set("session_id", session_factory(user).id)
    return user


def _lesson(db):
    event_type = create_type(db, name="Занятие", color="violet", style="fill")
    db.commit()
    return event_type


def _body(type_id, **extra):
    body = {
        "type_id": type_id, "title": "Тренировка рисунка", "note": None,
        "starts_on": "2026-10-09", "ends_on": "2026-10-09", "meeting_url": None,
    }
    body.update(extra)
    return body


def _new_digest(client, *, title, year, month):
    return client.post(PAGE, json={
        "title": title, "year": year, "month": month, "assign_to_all": True,
        "tag_ids": [], "assignee_usernames": "",
    }).json()["digest_id"]


def _payload(html):
    raw = re.search(r'id="digestEventsData">(.*?)</script>', html, re.S).group(1)
    return {item["title"]: item for item in json.loads(raw)}


def _list_titles(html):
    """Названия строк списка «События месяца» (без сетки и панели дня)."""
    tail = html.split('class="dgst-list"', 1)
    if len(tail) < 2:
        return []
    block = tail[1].split("</ul>", 1)[0]
    return [t.strip() for t in re.findall(r'class="dgst-event-title">\s*([^<]+?)\s*<', block)]


# ── Время ───────────────────────────────────────────────────────────────

def test_event_keeps_time_and_list_shows_it(client, db, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    lesson = _lesson(db)
    digest_id = _new_digest(client, title="Октябрь", year=2026, month=10)

    created = client.post(
        f"{PAGE}/{digest_id}/events",
        json=_body(lesson.id, time_from="10:00", time_to="11:30"),
    )
    assert created.status_code == 200
    event = db.get(ScheduleEvent, created.json()["event_id"])
    assert (event.time_from, event.time_to) == (time(10, 0), time(11, 30))

    html = client.get(f"{PAGE}/{digest_id}/events").text
    assert "Занятие · 10:00–11:30" in html
    item = _payload(html)["Тренировка рисунка"]
    assert (item["time_from"], item["time_to"], item["time"]) == ("10:00", "11:30", "10:00–11:30")

    # Пустые поля формы — события без времени: строка списка прежняя.
    updated = client.post(
        f"{PAGE}/{digest_id}/events/{event.id}", json=_body(lesson.id, time_from="", time_to=""),
    )
    assert updated.status_code == 200
    db.refresh(event)
    assert event.time_from is None and event.time_to is None
    assert "Занятие · " not in client.get(f"{PAGE}/{digest_id}/events").text


def test_time_rules(client, db, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    lesson = _lesson(db)
    digest_id = _new_digest(client, title="Октябрь", year=2026, month=10)
    url = f"{PAGE}/{digest_id}/events"

    # «до» без «с» и конец не позже начала в один день — отказ.
    assert client.post(url, json=_body(lesson.id, time_to="11:30")).status_code == 422
    assert client.post(url, json=_body(lesson.id, time_from="11:30", time_to="10:00")).status_code == 422
    assert client.post(url, json=_body(lesson.id, time_from="10:00", time_to="10:00")).status_code == 422
    # Только «с» — можно; у периода «до» относится к последнему дню.
    assert client.post(url, json=_body(lesson.id, time_from="19:00")).status_code == 200
    assert client.post(url, json=_body(
        lesson.id, title="Окно сдачи", starts_on="2026-10-20", ends_on="2026-10-25",
        time_from="18:00", time_to="12:00",
    )).status_code == 200


def test_format_event_time():
    event = ScheduleEvent(title="x", starts_on=date(2026, 10, 9), ends_on=date(2026, 10, 9))
    assert format_event_time(event) == ""
    event.time_from = time(9, 5)
    assert format_event_time(event) == "с 09:05"
    event.time_to = time(11, 30)
    assert format_event_time(event) == "09:05–11:30"


def test_create_update_audit_carries_time(client, db, user_factory, session_factory):
    from app.models.audit_log import AuditLog

    _staff(client, user_factory, session_factory)
    lesson = _lesson(db)
    digest_id = _new_digest(client, title="Октябрь", year=2026, month=10)
    client.post(f"{PAGE}/{digest_id}/events", json=_body(lesson.id, time_from="16:00", time_to="19:00"))
    row = db.query(AuditLog).filter(AuditLog.action == "digest_event_create").one()
    assert json.loads(row.details)["time"] == "16:00–19:00"


# ── Края месяцев в редакторе ────────────────────────────────────────────

def test_editor_shows_neighbor_events_on_grid_days(client, db, user_factory, session_factory):
    """Ноябрь 2026 начинается в воскресенье: сетка ноября — с 26 октября.
    События октябрьского дайджеста на 1 ноября и на 26 октября видны в
    ноябрьском редакторе; правятся они в октябрьском."""
    _staff(client, user_factory, session_factory)
    lesson = _lesson(db)
    october = _new_digest(client, title="Октябрь", year=2026, month=10)
    november = _new_digest(client, title="Ноябрь", year=2026, month=11)
    for title, day in (
        ("Разбор КР", "2026-11-01"),
        ("Тренировка 26-го", "2026-10-26"),
        ("Тренировка 9-го", "2026-10-09"),
    ):
        assert client.post(
            f"{PAGE}/{october}/events", json=_body(lesson.id, title=title, starts_on=day, ends_on=day),
        ).status_code == 200
    assert client.post(
        f"{PAGE}/{november}/events",
        json=_body(lesson.id, title="Тренировка 2-го", starts_on="2026-11-02", ends_on="2026-11-02"),
    ).status_code == 200

    html = client.get(f"{PAGE}/{november}/events").text
    payload = _payload(html)
    assert set(payload) == {"Разбор КР", "Тренировка 26-го", "Тренировка 2-го"}
    assert payload["Разбор КР"]["foreign_digest"] == {"id": october, "title": "Октябрь"}
    assert payload["Тренировка 2-го"]["foreign_digest"] is None
    # Список — только то, что задевает ноябрь; 26 октября видно в сетке.
    assert _list_titles(html) == ["Разбор КР", "Тренировка 2-го"]
    assert re.search(r'data-day="2026-10-26"\s+aria-label="26 число: Тренировка 26-го"', html)
    # Чужое событие правится в своём дайджесте — ссылкой на его день.
    assert f'href="/cabinet/staff/digest/{october}/events?day=2026-11-01">Изменить в «Октябрь»' in html

    # И наоборот: октябрьская сетка кончается 1 ноября и видит ноябрьское
    # 1-е, но не 2-е — его нет в сетке октября.
    assert client.post(
        f"{PAGE}/{november}/events",
        json=_body(lesson.id, title="Эфир 1-го", starts_on="2026-11-01", ends_on="2026-11-01"),
    ).status_code == 200
    october_payload = _payload(client.get(f"{PAGE}/{october}/events").text)
    assert "Эфир 1-го" in october_payload
    assert "Тренировка 2-го" not in october_payload


def test_neighbor_events_follow_the_tariff_view(client, db, user_factory, session_factory):
    from urllib.parse import quote

    _staff(client, user_factory, session_factory)
    lesson = _lesson(db)
    october = _new_digest(client, title="Октябрь", year=2026, month=10)
    november = _new_digest(client, title="Ноябрь", year=2026, month=11)
    client.post(f"{PAGE}/{october}/events", json=_body(
        lesson.id, title="Для максимума", starts_on="2026-11-01", ends_on="2026-11-01",
        tariffs=["УВЕРЕННЫЙ МАКСИМУМ"],
    ))
    assert "Для максимума" in _payload(client.get(f"{PAGE}/{november}/events").text)
    view = _payload(client.get(f"{PAGE}/{november}/events?tariff={quote('Я САМ')}").text)
    assert "Для максимума" not in view


def test_deleted_neighbor_digest_is_not_shown(client, db, user_factory, session_factory):
    _staff(client, user_factory, session_factory)
    lesson = _lesson(db)
    october = _new_digest(client, title="Октябрь", year=2026, month=10)
    november = _new_digest(client, title="Ноябрь", year=2026, month=11)
    client.post(f"{PAGE}/{october}/events", json=_body(
        lesson.id, title="Разбор КР", starts_on="2026-11-01", ends_on="2026-11-01",
    ))
    assert client.post(f"{PAGE}/{october}/delete").status_code == 200
    assert "Разбор КР" not in _payload(client.get(f"{PAGE}/{november}/events").text)


def test_digest_span_covers_whole_weeks():
    from app.models.tracker import ScheduleDigest

    assert digest_span(ScheduleDigest(year=2026, month=11)) == (date(2026, 10, 26), date(2026, 12, 6))
    assert digest_span(ScheduleDigest(year=2026, month=10)) == (date(2026, 9, 28), date(2026, 11, 1))


# ── Края месяцев у ученика ──────────────────────────────────────────────

def _student(client, user_factory, session_factory, *, tariff=None, vk_id=440_101):
    student = user_factory(vk_id=vk_id, name="Ученик", role_name="ученик")
    if tariff:
        student.tariff = tariff
    client.cookies.set("session_id", session_factory(student).id)
    return student


def _published(db, author_id, *, title, year, month, events):
    digest = create_digest(db, title=title, year=year, month=month, assign_to_all=True, user_id=author_id)
    lesson = create_type(db, name=f"Занятие {title}", color="sky", style="fill")
    for event_title, day, tariffs in events:
        event = create_event(
            db, digest.id, type_id=lesson.id, title=event_title, note=None,
            starts_on=day, ends_on=day, meeting_url=None,
        )
        set_event_tariffs(db, event, tariffs)
    publish_digest(digest, user_id=author_id)
    db.commit()
    return digest


def test_student_sees_previous_digest_event_on_first_day_of_month(client, db, user_factory, session_factory):
    """Событие на 1-е число текущего месяца, заведённое в дайджесте прошлого
    месяца, ученик видит в своём месяце — и в сетке, и в списке."""
    student = _student(client, user_factory, session_factory)
    today = today_msk()
    prev_year, prev_month = shift_month(today.year, today.month, -1)
    first = date(today.year, today.month, 1)
    _published(db, student.id, title="Прошлый", year=prev_year, month=prev_month,
               events=[("Разбор КР с прошлого месяца", first, [])])
    _published(db, student.id, title="Текущий", year=today.year, month=today.month,
               events=[("Своё событие", today, [])])

    html = client.get("/cabinet/tracker").text
    assert "Разбор КР с прошлого месяца" in _list_titles(html)
    assert "Своё событие" in _list_titles(html)
    assert f'data-day="{first.isoformat()}"' in html
    assert "dgst-event-edit" not in html


def test_student_edges_respect_tariff_and_publication(client, db, user_factory, session_factory):
    student = _student(client, user_factory, session_factory, tariff="Я САМ", vk_id=440_102)
    db.commit()
    today = today_msk()
    prev_year, prev_month = shift_month(today.year, today.month, -1)
    next_year, next_month = shift_month(today.year, today.month, 1)
    first = date(today.year, today.month, 1)
    _published(db, student.id, title="Прошлый", year=prev_year, month=prev_month, events=[
        ("Общее с края", first, []),
        ("Чужой тариф с края", first, ["УВЕРЕННЫЙ МАКСИМУМ"]),
    ])
    _published(db, student.id, title="Текущий", year=today.year, month=today.month, events=[])
    # Черновик следующего месяца ученику не виден — и его края тоже.
    draft = create_digest(db, title="Черновик", year=next_year, month=next_month,
                          assign_to_all=True, user_id=student.id)
    lesson = create_type(db, name="Черновое", color="pink", style="fill")
    create_event(db, draft.id, type_id=lesson.id, title="Из черновика",
                 note=None, starts_on=digest_span(draft)[0], ends_on=digest_span(draft)[0], meeting_url=None)
    db.commit()

    html = client.get("/cabinet/tracker").text
    assert "Общее с края" in html
    assert "Чужой тариф с края" not in html
    assert "Из черновика" not in html


# ── Перенос времени из названий (scripts/digest_event_times.py) ─────────

def test_split_title_takes_time_out_of_the_title():
    from scripts.digest_event_times import split_title

    assert split_title("тренировка РИСУНОК 10:00-11:30") == ("тренировка РИСУНОК", time(10), time(11, 30))
    assert split_title("Р+К очно 16:00-19:00") == ("Р+К очно", time(16), time(19))
    assert split_title("Занятие с 9:30 до 11:00") == ("Занятие", time(9, 30), time(11))
    assert split_title("Тренировка 10:00 – 11:30, онлайн") == ("Тренировка, онлайн", time(10), time(11, 30))
    assert split_title("Эфир в 19:00") == ("Эфир", time(19), None)
    # Без времени, только время и даты с точкой — не трогаются.
    assert split_title("Разбор КР/пробника") is None
    assert split_title("10:00-11:30") is None
    assert split_title("Сдача до 10.11") is None


# ── Несколько типов в один день — кружок на доли (05.10.2026) ───────────

def test_student_calendar_splits_the_circle_by_types(client, db, user_factory, session_factory):
    """Служба заботы второй раз 05.10.2026: «ребёнку нужно видеть это в
    календаре, а то у него только один цвет» — точка 5px под кружком на
    телефоне не читалась. Кружок дня делится на доли цветов типов."""
    student = _student(client, user_factory, session_factory, vk_id=440_103)
    today = today_msk()
    digest = create_digest(db, title="Месяц", year=today.year, month=today.month,
                           assign_to_all=True, user_id=student.id)
    publish = create_type(db, name="Публикация", color="sky", style="fill")
    lesson = create_type(db, name="Занятие", color="violet", style="fill")
    for event_type, title in ((publish, "2 неделя"), (lesson, "Рисунок"), (lesson, "Композиция")):
        create_event(db, digest.id, type_id=event_type.id, title=title, note=None,
                     starts_on=today, ends_on=today, meeting_url=None)
    publish_digest(digest, user_id=student.id)
    db.commit()

    html = client.get("/cabinet/tracker").text
    cell = html.split(f'data-day="{today.isoformat()}"', 1)[1].split("</div>", 1)[0]
    assert "dgst-cal-num has-dot is-split is-split-2" in cell
    assert cell.index("dgst-cal-part is-fill dgst-color--sky") < cell.index("dgst-cal-part is-fill dgst-color--violet")
    assert f'<span class="dgst-cal-digit">{today.day}</span>' in cell
    assert "dgst-cal-more" in cell
