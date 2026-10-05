"""Экран Главного преподавателя: дайджест-расписание месяца (/cabinet/staff/digest)."""

from app.models.tag import Tag, UserTag
from app.models.tracker import ScheduleDigest, ScheduleEvent

PAGE = "/cabinet/staff/digest"


def _staff_client(client, user_factory, session_factory, *, role_name="админ", vk_id=420_004):
    user = user_factory(
        vk_id=vk_id,
        name="Главный преподаватель",
        is_admin=role_name in ("админ", "суперадмин"),
        is_group_member=False,
        role_name=role_name,
    )
    session = session_factory(user)
    client.cookies.set("session_id", session.id)
    return user


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def _student_with_tag(db, user_factory, tag: Tag, *, vk_id: int):
    student = user_factory(vk_id=vk_id, name=f"Ученик {vk_id}", role_name="ученик")
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()
    return student


# ── Доступ ────────────────────────────────────────────────────────────────

def test_moderator_cannot_open_digest_admin(client, user_factory, session_factory):
    """Модератор — наблюдатель (решение владельца 28.09.2026): раздел программ ему закрыт."""
    _staff_client(client, user_factory, session_factory, role_name="модератор", vk_id=420_003)
    assert client.get(PAGE).status_code == 403


def test_admin_opens_digest_admin(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    response = client.get(PAGE)
    assert response.status_code == 200
    assert "Дайджест-расписание" in response.text


def test_student_cannot_open_digest_admin(auth_client):
    client, _ = auth_client
    assert client.get(PAGE).status_code == 403


# ── Создание и адресация ────────────────────────────────────────────────

def test_admin_creates_digest_with_real_audience(client, db, user_factory, session_factory):
    admin = _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "Поток 1")
    _student_with_tag(db, user_factory, tag, vk_id=421_001)

    response = client.post(
        PAGE,
        json={
            "title": "Сентябрь — поток 1",
            "year": 2026,
            "month": 9,
            "assign_to_all": False,
            "tag_ids": [tag.id],
            "assignee_usernames": "",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["audience_size"] == 1
    digest = db.query(ScheduleDigest).one()
    assert digest.created_by_id == admin.id
    assert digest.is_published is False
    assert digest.year == 2026
    assert digest.month == 9


def test_update_digest_rewrites_audience(client, db, user_factory, session_factory):
    admin = _staff_client(client, user_factory, session_factory)
    tag_a = _tag(db, "Поток A")
    tag_b = _tag(db, "Поток B")
    _student_with_tag(db, user_factory, tag_a, vk_id=421_002)
    _student_with_tag(db, user_factory, tag_b, vk_id=421_003)

    create_resp = client.post(
        PAGE,
        json={
            "title": "Сентябрь", "year": 2026, "month": 9,
            "assign_to_all": False, "tag_ids": [tag_a.id], "assignee_usernames": "",
        },
    )
    digest_id = create_resp.json()["digest_id"]

    update_resp = client.post(
        f"{PAGE}/{digest_id}",
        json={
            "title": "Сентябрь (обновлено)", "year": 2026, "month": 9,
            "assign_to_all": False, "tag_ids": [tag_b.id], "assignee_usernames": "",
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["audience_size"] == 1
    digest = db.get(ScheduleDigest, digest_id)
    assert digest.title == "Сентябрь (обновлено)"


# ── Публикация и удаление ───────────────────────────────────────────────

def test_publish_unpublish_and_delete_lifecycle(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    create_resp = client.post(
        PAGE,
        json={"title": "Октябрь", "year": 2026, "month": 10, "assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    )
    digest_id = create_resp.json()["digest_id"]

    # Опубликованный дайджест нельзя удалить сразу.
    publish_resp = client.post(f"{PAGE}/{digest_id}/publish")
    assert publish_resp.status_code == 200
    digest = db.get(ScheduleDigest, digest_id)
    assert digest.is_published is True

    delete_blocked = client.post(f"{PAGE}/{digest_id}/delete")
    assert delete_blocked.status_code == 409

    unpublish_resp = client.post(f"{PAGE}/{digest_id}/unpublish")
    assert unpublish_resp.status_code == 200
    db.refresh(digest)
    assert digest.is_published is False

    delete_resp = client.post(f"{PAGE}/{digest_id}/delete")
    assert delete_resp.status_code == 200
    db.refresh(digest)
    assert digest.deleted_at is not None


# ── События ──────────────────────────────────────────────────────────────

def _type(db, name="Пробник", color="teal", style="ring"):
    from app.services.schedule_event_types import create_type

    event_type = create_type(db, name=name, color=color, style=style)
    db.commit()
    return event_type


def test_events_crud_inside_digest(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    mock_type = _type(db)
    create_resp = client.post(
        PAGE,
        json={"title": "Ноябрь", "year": 2026, "month": 11, "assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    )
    digest_id = create_resp.json()["digest_id"]

    events_page = client.get(f"{PAGE}/{digest_id}/events")
    assert events_page.status_code == 200

    create_event_resp = client.post(
        f"{PAGE}/{digest_id}/events",
        json={
            "type_id": mock_type.id, "title": "Окно пробника",
            "note": None, "starts_on": "2026-11-25", "ends_on": "2026-11-30",
            "meeting_url": None, "sort_order": 0,
        },
    )
    assert create_event_resp.status_code == 200
    event_id = create_event_resp.json()["event_id"]
    event = db.get(ScheduleEvent, event_id)
    assert event.digest_id == digest_id
    assert event.type_id == mock_type.id
    assert event.starts_on.isoformat() == "2026-11-25"

    update_resp = client.post(
        f"{PAGE}/{digest_id}/events/{event_id}",
        json={
            "type_id": mock_type.id, "title": "Окно пробника (сдвинуто)",
            "note": "Финал", "starts_on": "2026-11-26", "ends_on": "2026-11-30",
            "meeting_url": "https://example.com/call", "sort_order": 0,
        },
    )
    assert update_resp.status_code == 200
    db.refresh(event)
    assert event.title == "Окно пробника (сдвинуто)"
    assert event.meeting_url == "https://example.com/call"

    delete_resp = client.post(f"{PAGE}/{digest_id}/events/{event_id}/delete")
    assert delete_resp.status_code == 200
    assert db.get(ScheduleEvent, event_id) is None


def test_event_ends_before_starts_is_rejected(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    deadline = _type(db, "Дедлайн", "pink", "fill")
    create_resp = client.post(
        PAGE,
        json={"title": "Декабрь", "year": 2026, "month": 12, "assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    )
    digest_id = create_resp.json()["digest_id"]

    response = client.post(
        f"{PAGE}/{digest_id}/events",
        json={
            "type_id": deadline.id, "title": "Дедлайн",
            "note": None, "starts_on": "2026-12-10", "ends_on": "2026-12-05",
            "meeting_url": None, "sort_order": 0,
        },
    )
    assert response.status_code == 422


def test_event_meeting_url_must_be_http_or_https(client, db, user_factory, session_factory):
    """Код-ревью 28.09.2026, P2: ссылка на созвон уходит в `href` у каждого
    ученика из адресатов дайджеста. Схема `javascript:` выполнила бы код у
    того, кто нажмёт; CSP с `'unsafe-inline'` её не останавливает."""
    _staff_client(client, user_factory, session_factory)
    deadline = _type(db, "Дедлайн", "pink", "fill")
    create_resp = client.post(
        PAGE,
        json={"title": "Октябрь", "year": 2026, "month": 10, "assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    )
    digest_id = create_resp.json()["digest_id"]

    response = client.post(
        f"{PAGE}/{digest_id}/events",
        json={
            "type_id": deadline.id, "title": "Созвон",
            "note": None, "starts_on": "2026-10-10", "ends_on": "2026-10-10",
            "meeting_url": "javascript:alert(document.cookie)", "sort_order": 0,
        },
    )

    assert response.status_code == 422
    assert db.query(ScheduleEvent).filter_by(digest_id=digest_id).count() == 0


# ── Тип события задаёт цвет; тарифы события (04.10.2026, 01.10.2026) ────────

def _digest_id(client):
    resp = client.post(
        PAGE,
        json={"title": "Октябрь", "year": 2026, "month": 10, "assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    )
    return resp.json()["digest_id"]


def _event_body(type_id, **extra):
    body = {
        "type_id": type_id, "title": "Разбор работ", "note": "Подготовьте финал",
        "starts_on": "2026-10-07", "ends_on": "2026-10-07", "meeting_url": None, "sort_order": 0,
    }
    body.update(extra)
    return body


def test_event_color_comes_from_its_type_and_tariffs_are_kept(client, db, user_factory, session_factory):
    from app.services.tracker import event_tariffs_map

    _staff_client(client, user_factory, session_factory)
    lesson = _type(db, "Занятие", "sky", "fill")
    broadcast = _type(db, "Общий эфир", "orange", "fill")
    digest_id = _digest_id(client)

    created = client.post(
        f"{PAGE}/{digest_id}/events",
        json=_event_body(lesson.id, tariffs=["Я С ВАМИ", "УВЕРЕННЫЙ МАКСИМУМ"]),
    )
    assert created.status_code == 200
    event_id = created.json()["event_id"]
    assert sorted(event_tariffs_map(db, [event_id])[event_id]) == ["УВЕРЕННЫЙ МАКСИМУМ", "Я С ВАМИ"]

    page = client.get(f"{PAGE}/{digest_id}/events")
    assert page.status_code == 200
    assert "dgst-cal dgst-cal--edit" in page.text
    assert 'data-day="2026-10-07"' in page.text
    assert "dgst-color--sky" in page.text

    # Сменили тип — сменился цвет; снятые галочки — снова «всем тарифам».
    updated = client.post(f"{PAGE}/{digest_id}/events/{event_id}", json=_event_body(broadcast.id, tariffs=[]))
    assert updated.status_code == 200
    page = client.get(f"{PAGE}/{digest_id}/events")
    assert "dgst-color--orange" in page.text
    assert event_tariffs_map(db, [event_id]) == {}


def test_recolouring_a_type_recolours_its_events(client, db, user_factory, session_factory):
    """Связка «тип + цвет» (владелец 04.10.2026): цвет у события не свой."""
    _staff_client(client, user_factory, session_factory)
    lesson = _type(db, "Занятие", "sky", "fill")
    digest_id = _digest_id(client)
    client.post(f"{PAGE}/{digest_id}/events", json=_event_body(lesson.id))

    response = client.post(f"{PAGE}/types/{lesson.id}", json={"name": "Занятие", "color": "pink", "style": "fill"})

    assert response.status_code == 200
    page = client.get(f"{PAGE}/{digest_id}/events")
    assert "dgst-color--pink" in page.text
    assert "dgst-color--sky" not in page.text


def test_event_rejects_unknown_type_and_unknown_tariff(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    lesson = _type(db, "Занятие", "sky", "fill")
    digest_id = _digest_id(client)

    bad_type = client.post(f"{PAGE}/{digest_id}/events", json=_event_body(9999))
    bad_tariff = client.post(f"{PAGE}/{digest_id}/events", json=_event_body(lesson.id, tariffs=["ПРЕМИУМ"]))
    old_color_field = client.post(f"{PAGE}/{digest_id}/events", json=_event_body(lesson.id, color="sky"))

    assert bad_type.status_code == 422
    assert bad_tariff.status_code == 422
    assert old_color_field.status_code == 422


def test_hidden_type_is_not_offered_but_old_event_keeps_it(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    old = _type(db, "Старый тип", "gray", "fill")
    digest_id = _digest_id(client)
    event_id = client.post(f"{PAGE}/{digest_id}/events", json=_event_body(old.id)).json()["event_id"]

    assert client.post(f"{PAGE}/types/{old.id}/archive").status_code == 200

    # Новое событие на скрытый тип не заводится…
    assert client.post(f"{PAGE}/{digest_id}/events", json=_event_body(old.id)).status_code == 422
    # …а старое правится, не меняя тип.
    edited = client.post(
        f"{PAGE}/{digest_id}/events/{event_id}", json=_event_body(old.id, title="Переименовано")
    )
    assert edited.status_code == 200
    page = client.get(f"{PAGE}/{digest_id}/events")
    assert f'name="event-type" value="{old.id}"' not in page.text
    assert "dgst-color--gray" in page.text


# ── Экран типов событий ────────────────────────────────────────────────────

def test_types_page_lists_types_with_usage(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    used = _type(db, "Пробник", "teal", "ring")
    _type(db, "Обратная связь", "mint", "fill")
    digest_id = _digest_id(client)
    client.post(f"{PAGE}/{digest_id}/events", json=_event_body(used.id))

    page = client.get(f"{PAGE}/types")

    assert page.status_code == 200
    assert "Пробник" in page.text and "Обратная связь" in page.text
    assert "событий: 1" in page.text
    assert "не используется" in page.text
    assert "dgst-chip is-ring dgst-color--teal" in page.text


def test_type_create_update_validation(client, db, user_factory, session_factory):
    from app.models.tracker import ScheduleEventType

    _staff_client(client, user_factory, session_factory)
    created = client.post(f"{PAGE}/types", json={"name": "  Публикация  ", "color": "sky", "style": "fill"})
    assert created.status_code == 200
    event_type = db.get(ScheduleEventType, created.json()["type_id"])
    assert event_type.name == "Публикация"

    assert client.post(f"{PAGE}/types", json={"name": "Х", "color": "#ff0000", "style": "fill"}).status_code == 422
    assert client.post(f"{PAGE}/types", json={"name": "Х", "color": "sky", "style": "dashed"}).status_code == 422
    assert client.post(f"{PAGE}/types", json={"name": "   ", "color": "sky", "style": "fill"}).status_code == 422

    updated = client.post(f"{PAGE}/types/{event_type.id}", json={"name": "Публикация уроков", "color": "violet", "style": "ring"})
    assert updated.status_code == 200
    db.refresh(event_type)
    assert (event_type.name, event_type.color, event_type.style) == ("Публикация уроков", "violet", "ring")


def test_used_type_cannot_be_deleted_only_hidden(client, db, user_factory, session_factory):
    from app.models.tracker import ScheduleEventType

    _staff_client(client, user_factory, session_factory)
    used = _type(db, "Пробник", "teal", "ring")
    unused = _type(db, "Лишний", "gray", "fill")
    digest_id = _digest_id(client)
    client.post(f"{PAGE}/{digest_id}/events", json=_event_body(used.id))

    refused = client.post(f"{PAGE}/types/{used.id}/delete")
    assert refused.status_code == 409
    assert refused.json()["error"] == "type_in_use"
    assert db.get(ScheduleEventType, used.id) is not None

    assert client.post(f"{PAGE}/types/{unused.id}/delete").status_code == 200
    db.expire_all()
    assert db.get(ScheduleEventType, unused.id) is None

    assert client.post(f"{PAGE}/types/{used.id}/archive").status_code == 200
    db.refresh(used)
    assert used.archived_at is not None
    assert client.post(f"{PAGE}/types/{used.id}/restore").status_code == 200
    db.refresh(used)
    assert used.archived_at is None


def test_type_move_changes_order(client, db, user_factory, session_factory):
    from app.services.schedule_event_types import list_types

    _staff_client(client, user_factory, session_factory)
    first = _type(db, "Первый", "sky", "fill")
    second = _type(db, "Второй", "pink", "fill")

    assert client.post(f"{PAGE}/types/{second.id}/move", json={"direction": -1}).status_code == 200

    db.expire_all()
    assert [t.name for t in list_types(db)] == ["Второй", "Первый"]
    assert first.id != second.id


def test_moderator_and_curator_cannot_touch_types(client, db, user_factory, session_factory):
    event_type = _type(db)
    _staff_client(client, user_factory, session_factory, role_name="модератор", vk_id=420_013)
    assert client.get(f"{PAGE}/types").status_code == 403
    assert client.post(f"{PAGE}/types/{event_type.id}/archive").status_code == 403

    _staff_client(client, user_factory, session_factory, role_name="куратор", vk_id=420_014)
    assert client.post(f"{PAGE}/types", json={"name": "Х", "color": "sky", "style": "fill"}).status_code == 403


def test_digest_scripts_call_only_defined_functions_and_parse(client, db, user_factory, session_factory):
    """Правило 11: зелёный сервер не значит рабочие кнопки. Каждая функция,
    которую зовут скрипты редактора месяца и экрана типов, объявлена, а сам
    код разбирается `node --check` (тот же сторож, что у страницы дня)."""
    import pathlib
    import re
    import shutil
    import subprocess
    import tempfile

    from test_program_day_script import KNOWN_GLOBALS, _strip_noise

    _staff_client(client, user_factory, session_factory)
    _type(db, "Занятие", "violet", "fill")
    digest_id = _digest_id(client)
    pages = [client.get(f"{PAGE}/{digest_id}/events").text, client.get(f"{PAGE}/types").text]
    node = shutil.which("node")

    for html in pages:
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        assert scripts
        for raw in scripts:
            js = _strip_noise(raw)
            declared = set(re.findall(r"function\s+(\w+)", js))
            declared |= set(re.findall(r"\bvar\s+(\w+)", js))
            for params in re.findall(r"function[^(]*\(([^)]*)\)", js):
                declared |= {p.strip() for p in params.split(",") if p.strip()}
            called = set(re.findall(r"(?<![.\w$])([A-Za-z_$]\w*)\s*\(", js))
            missing = sorted(called - declared - KNOWN_GLOBALS)
            assert not missing, f"вызовы без определения: {missing}"
            if node:
                with tempfile.TemporaryDirectory() as tmp:
                    path = pathlib.Path(tmp) / "digest.js"
                    path.write_text(raw, encoding="utf-8")
                    check = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
                    assert check.returncode == 0, check.stderr


def test_event_create_update_delete_leave_audit_trail(client, db, user_factory, session_factory):
    """Событие дайджеста оставляет след в журнале (05.10.2026). До этого
    двадцать событий 04.10 восстанавливали по `created_at` и логам запросов:
    у события нет ни автора, ни времени правки. Записи видны в «Журнале
    изменений» «Статистики активности» и подписаны словами, а не ключом."""
    import json

    from app.models.audit_log import AuditLog
    from app.services.activity_stats import get_audit_feed

    staff = _staff_client(client, user_factory, session_factory)
    lesson = _type(db, "Занятие", "violet", "fill")
    digest_id = _digest_id(client)

    event_id = client.post(
        f"{PAGE}/{digest_id}/events", json=_event_body(lesson.id, title="тренировка РИСУНОК")
    ).json()["event_id"]
    assert client.post(
        f"{PAGE}/{digest_id}/events/{event_id}",
        json=_event_body(lesson.id, title="Тренировка: рисунок", starts_on="2026-10-08", ends_on="2026-10-09"),
    ).status_code == 200
    assert client.post(f"{PAGE}/{digest_id}/events/{event_id}/delete").status_code == 200

    rows = (
        db.query(AuditLog)
        .filter(AuditLog.action.like("digest_event_%"))
        .order_by(AuditLog.id)
        .all()
    )
    assert [row.action for row in rows] == [
        "digest_event_create", "digest_event_update", "digest_event_delete",
    ]
    assert all(row.performed_by_id == staff.id for row in rows)
    assert json.loads(rows[0].details) == {
        "digest_id": digest_id, "event_id": event_id, "title": "тренировка РИСУНОК",
        "starts_on": "2026-10-07", "ends_on": "2026-10-07", "type": "Занятие",
    }
    updated = json.loads(rows[1].details)
    assert (updated["title"], updated["starts_on"], updated["ends_on"]) == (
        "Тренировка: рисунок", "2026-10-08", "2026-10-09",
    )
    assert json.loads(rows[2].details)["event_id"] == event_id

    labels = {item["action"]: item["action_label"] for item in get_audit_feed(db)}
    assert labels["digest_event_create"] == "Событие дайджеста: создано"
    assert labels["digest_event_delete"] == "Событие дайджеста: удалено"
    assert labels["digest_create"] == "Дайджест: создан"


def test_refused_event_save_leaves_no_audit_row(client, db, user_factory, session_factory):
    """Отказ по типу (422) в журнал не пишется: запись — только о сделанном."""
    from app.models.audit_log import AuditLog

    _staff_client(client, user_factory, session_factory)
    digest_id = _digest_id(client)
    assert client.post(f"{PAGE}/{digest_id}/events", json=_event_body(999_999)).status_code == 422
    assert db.query(AuditLog).filter(AuditLog.action.like("digest_event_%")).count() == 0


def test_editor_shows_the_month_of_one_tariff(client, db, user_factory, session_factory):
    """Служба заботы 04.10.2026: «видеть 3 отдельных календаря по тарифам,
    чтобы можно было делать скрин». `?tariff=` режет страницу тем же отбором,
    что у ученика: общие события плюс события тарифа — в сетке, списке и
    данных панели дня. Незнакомый тариф — все события."""
    import json
    import re
    from urllib.parse import quote

    _staff_client(client, user_factory, session_factory)
    lesson = _type(db, "Занятие", "violet", "fill")
    digest_id = _digest_id(client)
    for title, tariffs in (
        ("Публикация недели", []),
        ("Занятие для максимума", ["УВЕРЕННЫЙ МАКСИМУМ"]),
        ("Разбор для «Я сам»", ["Я САМ"]),
    ):
        assert client.post(
            f"{PAGE}/{digest_id}/events", json=_event_body(lesson.id, title=title, tariffs=tariffs)
        ).status_code == 200

    def titles(url):
        html = client.get(url).text
        payload = re.search(r'id="digestEventsData">(.*?)</script>', html, re.S).group(1)
        return html, sorted(item["title"] for item in json.loads(payload))

    html, everything = titles(f"{PAGE}/{digest_id}/events")
    assert everything == ["Занятие для максимума", "Публикация недели", "Разбор для «Я сам»"]
    assert 'aria-current="page">Все события' in html
    for label in ("Я сам", "Я с вами", "Уверенный максимум"):
        assert f">{label}</a>" in html

    html, self_only = titles(f"{PAGE}/{digest_id}/events?tariff={quote('Я САМ')}")
    assert self_only == ["Публикация недели", "Разбор для «Я сам»"]
    assert "Занятие для максимума" not in html
    assert 'aria-current="page">Я сам' in html

    _, with_you = titles(f"{PAGE}/{digest_id}/events?tariff={quote('Я С ВАМИ')}")
    assert with_you == ["Публикация недели"]

    _, unknown = titles(f"{PAGE}/{digest_id}/events?tariff=whatever")
    assert unknown == everything
