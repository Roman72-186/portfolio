"""Render contracts for exam assignment templates."""

from datetime import date, timedelta

from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee


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
