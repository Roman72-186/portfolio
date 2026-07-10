"""Render contracts for exam assignment templates."""

from datetime import date, timedelta

from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee
from app.models.tag import Tag
from app.services.tz import MSK_TZ


def _login_as(client, session_factory, user):
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)


def _create_assignment(db, user, *, status="published") -> ExamAssignment:
    assignment = ExamAssignment(
        title="Тестовое задание",
        subject="Рисунок",
        created_by_id=user.id,
        status=status,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def test_exam_assignments_hub_renders(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303001, role_name="суперадмин")
    _login_as(client, session_factory, admin)

    resp = client.get("/cabinet/exam-assignments")

    assert resp.status_code == 200
    assert "/cabinet/exam-assignments/active" in resp.text
    assert "/cabinet/exam-assignments/archive" in resp.text
    assert "/cabinet/exam-assignments/create" in resp.text


def test_exam_assignments_active_renders_status_badge(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303005, role_name="суперадмин")
    _login_as(client, session_factory, admin)
    _create_assignment(db, admin, status="published")

    resp = client.get("/cabinet/exam-assignments/active")

    assert resp.status_code == 200
    assert 'class="status-badge status-on"' in resp.text
    assert "Включён" in resp.text


def test_exam_assignments_disabled_shows_off_badge(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303006, role_name="суперадмин")
    _login_as(client, session_factory, admin)
    _create_assignment(db, admin, status="draft")

    resp = client.get("/cabinet/exam-assignments/active")

    assert resp.status_code == 200
    assert 'class="status-badge status-off"' in resp.text
    assert "Выключен" in resp.text


def test_exam_assignment_toggle_flips_status(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303007, role_name="суперадмин")
    _login_as(client, session_factory, admin)
    assignment = _create_assignment(db, admin, status="published")

    resp = client.post(
        f"/cabinet/exam-assignments/{assignment.id}/toggle",
        data={"csrf_token": "bypass"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db.refresh(assignment)
    assert assignment.status == "draft"  # выключен — сдача отклоняется

    client.post(
        f"/cabinet/exam-assignments/{assignment.id}/toggle",
        data={"csrf_token": "bypass"},
        follow_redirects=False,
    )
    db.refresh(assignment)
    assert assignment.status == "published"  # снова включён


def test_exam_assignment_activate_from_archive(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303008, role_name="суперадмин")
    _login_as(client, session_factory, admin)
    assignment = _create_assignment(db, admin, status="archived")

    resp = client.post(
        f"/cabinet/exam-assignments/{assignment.id}/activate",
        data={"csrf_token": "bypass"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cabinet/exam-assignments/active"
    db.refresh(assignment)
    assert assignment.status == "published"


def test_exam_assignment_create_saves_exact_time_timer_and_target_tag(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303011, role_name="суперадмин")
    _login_as(client, session_factory, admin)
    tag = Tag(name="Пробник Р")
    db.add(tag)
    db.commit()
    db.refresh(tag)

    day = (date.today() + timedelta(days=3)).isoformat()
    resp = client.post(
        "/cabinet/exam-assignments/create",
        data={
            "csrf_token": "bypass",
            "title": "Пробник с расписанием",
            "subject": "Рисунок",
            "ticket_count": "1",
            "ticket_1_title": "Билет 1",
            "ticket_1_description": "Описание",
            "ticket_1_opens_at": f"{day}T11:45",
            "ticket_1_closes_at": f"{day}T18:00",
            "ticket_1_duration_minutes": "75",
            "ticket_1_target_tag_id": str(tag.id),
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    ticket = db.query(ExamTicket).filter(ExamTicket.title == "Билет 1").one()
    assert ticket.target_tag_id == tag.id
    assert ticket.duration_minutes == 75
    assert ticket.assign_to_all is False
    assert ticket.opens_at.astimezone(MSK_TZ).strftime("%Y-%m-%dT%H:%M") == f"{day}T11:45"
    assert ticket.closes_at.astimezone(MSK_TZ).strftime("%Y-%m-%dT%H:%M") == f"{day}T18:00"


def test_exam_assignment_create_without_tag_assigns_to_all(
    client,
    db,
    user_factory,
    session_factory,
):
    """Тег необязателен: билет без тега и без конкретных учеников → выдаётся всем."""
    admin = user_factory(vk_id=303012, role_name="суперадмин")
    _login_as(client, session_factory, admin)

    resp = client.post(
        "/cabinet/exam-assignments/create",
        data={
            "csrf_token": "bypass",
            "title": "Пробник без тега",
            "subject": "Рисунок",
            "ticket_count": "1",
            "ticket_1_title": "Билет без тега",
            "ticket_1_opens_at": f"{(date.today() + timedelta(days=3)).isoformat()}T11:45",
            "ticket_1_closes_at": f"{(date.today() + timedelta(days=3)).isoformat()}T18:00",
            "ticket_1_duration_minutes": "90",
            "ticket_1_target_tag_id": "",  # тег не выбран
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    ticket = db.query(ExamTicket).filter(ExamTicket.title == "Билет без тега").one()
    assert ticket.target_tag_id is None
    assert ticket.assign_to_all is True
    # Чекбокс не передан (как обычный неотмеченный HTML-чекбокс) — по умолчанию
    # получение билета НЕ ограничивается «временем на выполнение».
    assert ticket.restrict_start_by_duration is False


def test_exam_assignment_create_saves_ten_tickets(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303014, role_name="суперадмин")
    _login_as(client, session_factory, admin)

    day = (date.today() + timedelta(days=3)).isoformat()
    data = {
        "csrf_token": "bypass",
        "subject": "Рисунок",
        "ticket_count": "10",
    }
    for n in range(1, 11):
        data.update({
            f"ticket_{n}_title": f"Билет {n}",
            f"ticket_{n}_opens_at": f"{day}T11:45",
            f"ticket_{n}_closes_at": f"{day}T18:00",
            f"ticket_{n}_duration_minutes": "90",
        })

    resp = client.post(
        "/cabinet/exam-assignments/create",
        data=data,
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assignment = db.query(ExamAssignment).order_by(ExamAssignment.id.desc()).first()
    tickets = (
        db.query(ExamTicket)
        .filter(ExamTicket.assignment_id == assignment.id)
        .order_by(ExamTicket.ticket_number)
        .all()
    )
    assert [ticket.ticket_number for ticket in tickets] == list(range(1, 11))
    assert [ticket.title for ticket in tickets] == [f"Билет {n}" for n in range(1, 11)]


def test_exam_assignment_create_restrict_start_by_duration_checkbox(
    client,
    db,
    user_factory,
    session_factory,
):
    """Чекбокс "Запретить получение билета..." включает отсечку closes_at-duration."""
    admin = user_factory(vk_id=303013, role_name="суперадмин")
    _login_as(client, session_factory, admin)

    resp = client.post(
        "/cabinet/exam-assignments/create",
        data={
            "csrf_token": "bypass",
            "title": "Пробник с отсечкой получения",
            "subject": "Рисунок",
            "ticket_count": "1",
            "ticket_1_title": "Билет с отсечкой",
            "ticket_1_opens_at": f"{(date.today() + timedelta(days=3)).isoformat()}T11:45",
            "ticket_1_closes_at": f"{(date.today() + timedelta(days=3)).isoformat()}T18:00",
            "ticket_1_duration_minutes": "90",
            "ticket_1_restrict_start_by_duration": "on",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    ticket = db.query(ExamTicket).filter(ExamTicket.title == "Билет с отсечкой").one()
    assert ticket.restrict_start_by_duration is True


def test_exam_assignment_create_form_renders_student_selector(
    client,
    db,
    user_factory,
    session_factory,
):
    """Форма создания должна давать выбор «кому выдать» с режимом точечной выдачи."""
    admin = user_factory(vk_id=303014, role_name="суперадмин")
    _login_as(client, session_factory, admin)

    resp = client.get("/cabinet/exam-assignments/create")

    assert resp.status_code == 200
    assert "_assign_mode" in resp.text          # радио «Кому выдать билет»
    assert "Конкретным" in resp.text            # режим точечной выдачи
    assert "initStudentSearch" in resp.text     # рендер чекбоксов учеников


def test_exam_assignment_form_student_list_includes_username(
    client,
    db,
    user_factory,
    session_factory,
):
    """Список учеников для точечной выдачи отдаёт username (для поиска по нему);
    проверяет и расшифровку EncryptedString в колонночном запросе."""
    admin = user_factory(vk_id=303020, role_name="суперадмин")
    student = user_factory(vk_id=303021, role_name="ученик")
    student.last_name = "Иванов"
    student.first_name = "Пётр"
    student.tg_username = "petya_art"
    db.commit()
    _login_as(client, session_factory, admin)

    resp = client.get("/cabinet/exam-assignments/create")

    assert resp.status_code == 200
    # username (ASCII) виден буквально в student_list | tojson; ФИО — кириллица,
    # экранируется в \uXXXX, поэтому проверяем именно username.
    assert "petya_art" in resp.text          # username доступен фронту для поиска


def test_exam_assignment_create_assigns_to_specific_students(
    client,
    db,
    user_factory,
    session_factory,
):
    """Режим «Конкретным ученикам»: билет выдаётся только выбранным (ExamTicketAssignee)."""
    admin = user_factory(vk_id=303015, role_name="суперадмин")
    s1 = user_factory(vk_id=303016, role_name="ученик")
    s2 = user_factory(vk_id=303017, role_name="ученик")
    _login_as(client, session_factory, admin)

    # Окно в будущем относительно сегодня, чтобы не упереться в «время уже в прошлом».
    future = date.today() + timedelta(days=3)
    resp = client.post(
        "/cabinet/exam-assignments/create",
        data={
            "csrf_token": "bypass",
            "title": "Точечный пробник",
            "subject": "Рисунок",
            "ticket_count": "1",
            "ticket_1_title": "Билет для двоих",
            "ticket_1_opens_at": f"{future.isoformat()}T11:45",
            "ticket_1_closes_at": f"{future.isoformat()}T18:00",
            "ticket_1_duration_minutes": "90",
            "ticket_1_target_tag_id": "",          # без тега
            "ticket_1_student_ids": f"{s1.id},{s2.id}",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    ticket = db.query(ExamTicket).filter(ExamTicket.title == "Билет для двоих").one()
    assert ticket.target_tag_id is None
    assert ticket.assign_to_all is False        # НЕ выдан всем
    assignee_ids = {
        uid for (uid,) in db.query(ExamTicketAssignee.user_id)
        .filter(ExamTicketAssignee.ticket_id == ticket.id).all()
    }
    assert assignee_ids == {s1.id, s2.id}


def test_exam_assignment_duplicate_copies_tickets_and_assignees(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303010, role_name="суперадмин")
    student = user_factory(vk_id=303011, role_name="ученик")
    _login_as(client, session_factory, admin)

    source = _create_assignment(db, admin, status="published")
    # Билет 1 — назначен конкретному ученику (с notified_at)
    from datetime import datetime, timezone
    t1 = ExamTicket(
        assignment_id=source.id, ticket_number=1, title="Натюрморт",
        description="Описание", image_s3_url="http://s3/img.jpg",
        image_s3_path="tickets/img.jpg",
        start_date=date.today(), end_date=date.today() + timedelta(days=5),
        assign_to_all=False,
    )
    # Билет 2 — всем
    t2 = ExamTicket(
        assignment_id=source.id, ticket_number=2, title="Пейзаж",
        start_date=date.today(), end_date=date.today() + timedelta(days=5),
        assign_to_all=True,
    )
    db.add_all([t1, t2])
    db.commit()
    db.add(ExamTicketAssignee(
        ticket_id=t1.id, user_id=student.id,
        notified_at=datetime.now(timezone.utc),
    ))
    db.commit()

    resp = client.post(
        f"/cabinet/exam-assignments/{source.id}/duplicate",
        data={"csrf_token": "bypass"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    copy = (
        db.query(ExamAssignment)
        .filter(ExamAssignment.id != source.id)
        .order_by(ExamAssignment.id.desc())
        .first()
    )
    assert copy is not None
    assert copy.id != source.id
    assert copy.status == "draft"             # копия выключена
    assert copy.title == "Тестовое задание (копия)"
    assert copy.subject == source.subject
    assert resp.headers["location"] == f"/cabinet/exam-assignments/{copy.id}/edit"

    copy_tickets = (
        db.query(ExamTicket)
        .filter(ExamTicket.assignment_id == copy.id)
        .order_by(ExamTicket.ticket_number)
        .all()
    )
    assert len(copy_tickets) == 2
    assert copy_tickets[0].title == "Натюрморт"
    assert copy_tickets[0].image_s3_url == "http://s3/img.jpg"
    assert copy_tickets[0].assign_to_all is False
    assert copy_tickets[1].assign_to_all is True

    # Назначения скопированы, но notified_at сброшен
    copy_assignees = (
        db.query(ExamTicketAssignee)
        .filter(ExamTicketAssignee.ticket_id == copy_tickets[0].id)
        .all()
    )
    assert len(copy_assignees) == 1
    assert copy_assignees[0].user_id == student.id
    assert copy_assignees[0].notified_at is None

    # Источник не тронут
    db.refresh(source)
    assert source.status == "published"
    assert (
        db.query(ExamTicket).filter(ExamTicket.assignment_id == source.id).count() == 2
    )


def _create_form_data(**overrides) -> dict:
    future = date.today() + timedelta(days=3)
    data = {
        "csrf_token": "bypass",
        "subject": "Рисунок",
        "ticket_count": "1",
        "ticket_1_title": "Билет 1",
        "ticket_1_opens_at": f"{future.isoformat()}T11:45",
        "ticket_1_closes_at": f"{future.isoformat()}T18:00",
        "ticket_1_duration_minutes": "90",
    }
    data.update(overrides)
    return data


def test_exam_assignment_create_auto_title_and_seq_for_kind(
    client, db, user_factory, session_factory,
):
    """Тип «Контрольная» → kind/seq_number сохранены, title собран автоматически."""
    from datetime import datetime, timezone
    admin = user_factory(vk_id=303030, role_name="суперадмин")
    _login_as(client, session_factory, admin)

    resp = client.post(
        "/cabinet/exam-assignments/create",
        data=_create_form_data(kind="control", note="выпускной", ticket_1_title="К-Билет"),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    a = db.query(ExamAssignment).order_by(ExamAssignment.id.desc()).first()
    assert a.kind == "control"
    assert a.seq_number == 1
    assert a.note == "выпускной"
    today = datetime.now(timezone.utc).astimezone(MSK_TZ).strftime("%d.%m.%Y")
    assert a.title == f"Контрольная №1 · Рисунок · {today} · выпускной"


def test_exam_assignment_seq_number_per_kind_and_subject(
    client, db, user_factory, session_factory,
):
    """Нумерация сквозная в пределах (kind, subject); другой предмет/тип — своя серия."""
    admin = user_factory(vk_id=303031, role_name="суперадмин")
    _login_as(client, session_factory, admin)

    def _create(kind, subject, t):
        return client.post(
            "/cabinet/exam-assignments/create",
            data=_create_form_data(kind=kind, subject=subject, ticket_1_title=t),
            follow_redirects=False,
        )

    _create("mock", "Рисунок", "a")
    _create("mock", "Рисунок", "b")
    _create("control", "Рисунок", "c")
    _create("mock", "Композиция", "d")

    seqs = {
        (a.kind, a.subject): a.seq_number
        for a in db.query(ExamAssignment).all()
    }
    # Пробник Рисунок дошёл до 2, остальные серии — со своей единицы.
    assert max(
        a.seq_number for a in db.query(ExamAssignment)
        .filter(ExamAssignment.kind == "mock", ExamAssignment.subject == "Рисунок").all()
    ) == 2
    assert seqs[("control", "Рисунок")] == 1
    assert seqs[("mock", "Композиция")] == 1


def test_exam_assignment_edit_form_renders(
    client, db, user_factory, session_factory,
):
    """Edit-форма рендерится с типом/номером существующего задания."""
    admin = user_factory(vk_id=303040, role_name="суперадмин")
    _login_as(client, session_factory, admin)
    a = _create_assignment(db, admin, status="published")
    a.kind = "control"
    a.seq_number = 7
    a.note = "повтор"
    db.commit()

    resp = client.get(f"/cabinet/exam-assignments/{a.id}/edit")
    assert resp.status_code == 200
    assert 'value="control"' in resp.text and "checked" in resp.text
    assert "auto-title-preview" in resp.text


def test_exam_assignment_form_renders_recipient_filter(
    client, db, user_factory, session_factory,
):
    """Форма отдаёт конструктор подбора получателей и данные кураторов."""
    admin = user_factory(vk_id=303032, role_name="суперадмин")
    curator = user_factory(vk_id=303033, role_name="куратор")
    curator.last_name = "Кураторов"
    curator.first_name = "Иван"
    db.commit()
    _login_as(client, session_factory, admin)

    resp = client.get("/cabinet/exam-assignments/create")
    assert resp.status_code == 200
    assert "Подобрать по фильтру" in resp.text       # панель конструктора
    assert "buildRecipientFilter" in resp.text       # JS рендер фильтра
    assert 'name="kind"' in resp.text                # сегментный выбор типа
    assert "auto-title-preview" in resp.text         # превью авто-названия


def test_exam_assignment_duplicate_404_for_missing(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303012, role_name="суперадмин")
    _login_as(client, session_factory, admin)

    resp = client.post(
        "/cabinet/exam-assignments/999999/duplicate",
        data={"csrf_token": "bypass"},
        follow_redirects=False,
    )
    assert resp.status_code == 404


def test_exam_assignment_detail_renders_status_badge(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303002, role_name="суперадмин")
    _login_as(client, session_factory, admin)
    assignment = _create_assignment(db, admin, status="draft")

    resp = client.get(f"/cabinet/exam-assignments/{assignment.id}")

    assert resp.status_code == 200
    assert 'class="status-badge status-draft"' in resp.text
    assert "Черновик" in resp.text
