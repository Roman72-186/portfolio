"""Экран задач трекера у Главного преподавателя (ранг роли >= 4)."""

import re
from datetime import datetime, timedelta, timezone

from app.models.tag import Tag, UserTag
from app.models.tracker import TrackerTask, TrackerTaskAssignee, TrackerTaskTag
from app.services.tracker import count_task_audience


PAGE = "/cabinet/staff/tracker"
TASKS = "/cabinet/staff/tracker/tasks"


def _staff_client(client, user_factory, session_factory, *, role_name="админ", vk_id=410_004):
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


def _student_with_tag(db, user_factory, tag: Tag, *, vk_id: int, username: str | None = None):
    student = user_factory(vk_id=vk_id, name=f"Ученик {vk_id}", role_name="ученик")
    if username:
        student.tg_username = username
    db.add(UserTag(user_id=student.id, tag_id=tag.id))
    db.commit()
    return student


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


# ── Доступ ────────────────────────────────────────────────────────────────

def test_moderator_cannot_open_tracker_admin(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory, role_name="модератор", vk_id=410_003)
    assert client.get(PAGE).status_code == 403


def test_admin_and_superadmin_open_tracker_admin(
    client, user_factory, session_factory, admin_client
):
    _staff_client(client, user_factory, session_factory)
    rank4 = client.get(PAGE)
    assert rank4.status_code == 200
    assert "Задачи трекера" in rank4.text
    assert "/static/css/tracker.css?v=30" in rank4.text

    super_client, _ = admin_client
    assert super_client.get(PAGE).status_code == 200


def test_student_cannot_open_tracker_admin(auth_client):
    client, _ = auth_client
    assert client.get(PAGE).status_code == 403


# ── Создание и адресация ──────────────────────────────────────────────────

def test_admin_creates_task_and_sees_real_audience(
    client, db, user_factory, session_factory
):
    admin = _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "Поток 1")
    _student_with_tag(db, user_factory, tag, vk_id=411_001)

    response = client.post(
        TASKS,
        json={
            "title": "Сдать работу по перспективе",
            "description": "Два листа",
            "due_at": "2026-09-01T18:00",
            "starts_at": "",
            "subject": "Рисунок",
            "assign_to_all": False,
            "tag_ids": [tag.id],
            "assignee_usernames": "",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["audience_size"] == 1
    task = db.query(TrackerTask).one()
    assert task.created_by_id == admin.id
    assert task.is_published is False       # публикация отдельным шагом
    assert task.completion_mode == "auto_or_manual"
    assert task.subject == "Рисунок"
    assert [row.tag_id for row in db.query(TrackerTaskTag).all()] == [tag.id]


def test_unknown_subject_is_rejected(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        TASKS,
        json={"title": "Задача", "subject": "Черчение", "assign_to_all": True},
    )

    assert response.status_code == 422


def test_task_cannot_start_after_its_deadline(client, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        TASKS,
        json={
            "title": "Задача наоборот",
            "due_at": "2026-09-01T10:00",
            "starts_at": "2026-09-05T10:00",
            "assign_to_all": True,
        },
    )

    assert response.status_code == 422


def test_named_assignees_survive_a_plain_edit(
    client, db, user_factory, session_factory
):
    """Правка задачи не должна снимать её у поимённо добавленных.

    На темах видеоуроков это уже случалось: форма открывалась с пустым полем,
    сохранение переписывало список целиком и доступ пропадал молча.
    """
    _staff_client(client, user_factory, session_factory)
    tag = _tag(db, "Поток 2")
    _student_with_tag(db, user_factory, tag, vk_id=412_001)
    catching_up = user_factory(vk_id=412_002, name="Догоняющий", role_name="ученик")
    catching_up.tg_username = "dogonyayushiy"
    db.commit()

    created = client.post(
        TASKS,
        json={
            "title": "Досмотреть видео",
            "assign_to_all": False,
            "tag_ids": [tag.id],
            "assignee_usernames": "@dogonyayushiy",
        },
    ).json()
    assert created["not_found"] == []
    task_id = created["task_id"]
    assert created["audience_size"] == 2

    page = client.get(PAGE)
    assert "@dogonyayushiy" in page.text      # форма правки предзаполнена

    client.post(
        f"{TASKS}/{task_id}",
        json={
            "title": "Досмотреть видео до конца",
            "assign_to_all": False,
            "tag_ids": [tag.id],
            "assignee_usernames": "@dogonyayushiy",
        },
    )

    assignees = db.query(TrackerTaskAssignee).filter_by(task_id=task_id).all()
    assert [row.user_id for row in assignees] == [catching_up.id]


def test_unknown_username_is_reported_back(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        TASKS,
        json={
            "title": "Задача",
            "assign_to_all": False,
            "tag_ids": [],
            "assignee_usernames": "@нет_такого",
        },
    )

    assert response.json()["not_found"] == ["нет_такого"]
    assert response.json()["audience_size"] == 0


def test_tag_matching_is_strict_without_subject_heuristics(
    client, db, user_factory, session_factory
):
    """Тег «Р» не должен цеплять учеников с тегом «Р+К».

    Эвристика предметов из пробников на проде означает группу и уровень
    куратора и уже прятала билеты от учеников (TODO.md §7).
    """
    _staff_client(client, user_factory, session_factory)
    exact = _tag(db, "Р")
    combined = _tag(db, "Р+К")
    _student_with_tag(db, user_factory, exact, vk_id=413_001)
    _student_with_tag(db, user_factory, combined, vk_id=413_002)

    response = client.post(
        TASKS,
        json={"title": "Задача по рисунку", "assign_to_all": False, "tag_ids": [exact.id]},
    )

    data = response.json()
    assert data["audience_size"] == 1
    assert data["ambiguous_tags"] == ["Р"]     # предупреждение, а не правило доступа
    assert "означают группу и уровень куратора" in client.get(PAGE).text


def test_audience_counts_students_outside_the_group(db, user_factory):
    """Экран ученика закрыт require_student, без гейта по членству в группе.

    Отличие от count_topic_audience видеомодуля намеренное: там доступ режет
    require_learning_content_access, здесь такого гейта нет, и фильтр по
    is_group_member занизил бы охват.
    """
    outsider = user_factory(vk_id=414_001, role_name="ученик", is_group_member=False)
    assert outsider.is_group_member is False

    assert count_task_audience(db, assign_to_all=True, tag_ids=[], assignee_ids=[]) == 1


# ── Даты ──────────────────────────────────────────────────────────────────

def test_deadline_does_not_shift_on_a_no_op_resave(
    client, db, user_factory, session_factory
):
    """Сохранение того, что страница показала в форме, не должно двигать срок.

    Колонка TIMESTAMPTZ приезжает в таймзоне сессии, а форма трактует ввод как
    МСК: без обратного пересчёта каждая правка уводила бы дедлайн на три часа.
    Проверяем именно круг «страница → форма → сохранение», а не точную строку:
    SQLite в тестах хранит время без таймзоны, Postgres на проде переводит его в
    UTC, поэтому сама строка у двух движков разная, а вот съезжать она не имеет
    права ни там, ни там.
    """
    _staff_client(client, user_factory, session_factory)
    task_id = client.post(
        TASKS,
        json={"title": "Задача со сроком", "due_at": "2026-09-01T18:00", "assign_to_all": True},
    ).json()["task_id"]

    first = db.get(TrackerTask, task_id).due_at

    page = client.get(PAGE).text
    form_value = re.search(r'data-due="([^"]*)"', page).group(1)
    assert form_value

    client.post(
        f"{TASKS}/{task_id}",
        json={"title": "Задача со сроком", "due_at": form_value, "assign_to_all": True},
    )
    db.expire_all()

    assert db.get(TrackerTask, task_id).due_at == first


def test_task_without_deadline_is_allowed(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)

    response = client.post(
        TASKS, json={"title": "Задача без срока", "assign_to_all": True}
    )

    assert response.status_code == 200
    task = db.query(TrackerTask).one()
    assert task.due_at is None
    assert task.starts_at is None


# ── Публикация и удаление ─────────────────────────────────────────────────

def test_publish_and_unpublish_toggle_visibility(
    client, db, user_factory, session_factory
):
    admin = _staff_client(client, user_factory, session_factory)
    task_id = client.post(
        TASKS, json={"title": "Задача к публикации", "assign_to_all": True}
    ).json()["task_id"]

    client.post(f"{TASKS}/{task_id}/publish")
    db.expire_all()
    task = db.get(TrackerTask, task_id)
    assert task.is_published is True
    assert task.published_by_id == admin.id
    assert task.published_at is not None

    client.post(f"{TASKS}/{task_id}/unpublish")
    db.expire_all()
    task = db.get(TrackerTask, task_id)
    assert task.is_published is False
    assert task.published_at is None
    assert task.published_by_id is None


def test_published_task_cannot_be_deleted_before_it_is_hidden(
    client, db, user_factory, session_factory
):
    _staff_client(client, user_factory, session_factory)
    task_id = client.post(
        TASKS, json={"title": "Живая задача", "assign_to_all": True}
    ).json()["task_id"]
    client.post(f"{TASKS}/{task_id}/publish")

    blocked = client.post(f"{TASKS}/{task_id}/delete")
    assert blocked.status_code == 409
    assert blocked.json()["error"] == "unpublish_first"

    client.post(f"{TASKS}/{task_id}/unpublish")
    assert client.post(f"{TASKS}/{task_id}/delete").status_code == 200

    db.expire_all()
    assert db.get(TrackerTask, task_id).deleted_at is not None
    assert client.get(PAGE).text.count("Живая задача") == 0


def test_deleted_task_is_not_reachable(client, db, user_factory, session_factory):
    _staff_client(client, user_factory, session_factory)
    task = TrackerTask(
        title="Удалённая",
        deleted_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(task)
    db.commit()

    assert client.post(f"{TASKS}/{task.id}/publish").status_code == 404
    assert client.post(f"{TASKS}/{task.id}", json={"title": "Опять", "assign_to_all": True}).status_code == 404
