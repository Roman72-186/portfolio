"""Render contracts for exam assignment templates."""

from app.models.exam_assignment import ExamAssignment


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


def test_exam_assignments_list_renders_status_badge(
    client,
    db,
    user_factory,
    session_factory,
):
    admin = user_factory(vk_id=303001, role_name="суперадмин")
    _login_as(client, session_factory, admin)
    _create_assignment(db, admin, status="published")

    resp = client.get("/cabinet/exam-assignments")

    assert resp.status_code == 200
    assert 'class="status-badge status-published"' in resp.text
    assert "Опубликовано" in resp.text


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
