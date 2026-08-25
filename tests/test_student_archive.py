"""Архив учеников: отправка в архив, чтение архива суперадмином, запрет правок.

Смысл механики — выпуск потока: ученики уходят из рабочих списков, но их
работы, оценки и переписки остаются целыми и открыты суперадмину.
"""
import pytest

from app.models.user import User
from app.services.user_management import archive_user, toggle_user_active, unarchive_user


@pytest.fixture()
def superadmin_client(client, user_factory, session_factory):
    user = user_factory(vk_id=910001, name="Super Admin", role_name="суперадмин")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    return client, user


@pytest.fixture()
def student(user_factory, db):
    s = user_factory(vk_id=910002, name="Ученик Прошлого Потока")
    s.first_name = "Иван"
    s.last_name = "Прошлый"
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ── сервис ───────────────────────────────────────────────────────────────────

def test_archive_sets_flag_and_deactivates(db, student, user_factory):
    actor = user_factory(vk_id=910003, name="SA", role_name="суперадмин")
    assert archive_user(db, target_user_id=student.id, performed_by_id=actor.id) is True

    db.refresh(student)
    assert student.archived_at is not None
    assert student.is_active is False
    assert student.deleted_at is None


def test_archive_is_idempotent(db, student, user_factory):
    actor = user_factory(vk_id=910004, name="SA", role_name="суперадмин")
    assert archive_user(db, target_user_id=student.id, performed_by_id=actor.id) is True
    assert archive_user(db, target_user_id=student.id, performed_by_id=actor.id) is False


def test_unarchive_restores_access(db, student, user_factory):
    actor = user_factory(vk_id=910005, name="SA", role_name="суперадмин")
    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)
    assert unarchive_user(db, target_user_id=student.id, performed_by_id=actor.id) is True

    db.refresh(student)
    assert student.archived_at is None
    assert student.is_active is True


def test_toggle_active_refuses_archived(db, student, user_factory):
    """Разблокировка архивного вернула бы его в рабочие списки, оставив в архиве."""
    actor = user_factory(vk_id=910006, name="SA", role_name="суперадмин")
    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)

    assert toggle_user_active(db, target_user_id=student.id, performed_by_id=actor.id) is None
    db.refresh(student)
    assert student.is_active is False


def test_archive_invalidates_sessions(db, student, user_factory, session_factory):
    actor = user_factory(vk_id=910007, name="SA", role_name="суперадмин")
    sess = session_factory(student)

    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)

    db.refresh(sess)
    assert sess.is_active is False


# ── списки и страницы ────────────────────────────────────────────────────────

def test_archived_student_leaves_working_list(superadmin_client, db, student):
    client, actor = superadmin_client
    resp = client.get("/cabinet/students")
    assert resp.status_code == 200
    assert "Прошлый" in resp.text

    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)

    resp = client.get("/cabinet/students")
    assert resp.status_code == 200
    assert "Прошлый" not in resp.text


def test_archive_page_shows_archived_only(superadmin_client, db, student, user_factory):
    client, actor = superadmin_client
    current = user_factory(vk_id=910008, name="Действующий Ученик")
    current.last_name = "Текущий"
    db.add(current)
    db.commit()

    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)

    resp = client.get("/cabinet/archive")
    assert resp.status_code == 200
    assert "Прошлый" in resp.text
    assert "Текущий" not in resp.text


def test_archive_page_forbidden_for_curator(client, user_factory, session_factory):
    curator = user_factory(vk_id=910009, name="Куратор", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)

    assert client.get("/cabinet/archive").status_code == 403


def test_archived_student_data_readable_but_not_writable(superadmin_client, db, student):
    client, actor = superadmin_client
    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)

    # Чтение — работает: работы, статистика, портфолио на месте
    assert client.get(f"/cabinet/students/{student.id}/portfolio").status_code == 200
    assert client.get(f"/cabinet/students/{student.id}/mock-exams").status_code == 200
    assert client.get(f"/cabinet/students/{student.id}/statistics").status_code == 200

    # Запись — нет: архив не переписывается задним числом
    resp = client.post(f"/cabinet/students/{student.id}/profile", data={"tariff": "МАКСИМУМ"})
    assert resp.status_code == 404


def test_blocked_student_stays_unreachable(superadmin_client, db, student):
    """Блокировка — не архив: заблокированный не открывается и суперадмину."""
    client, actor = superadmin_client
    toggle_user_active(db, target_user_id=student.id, performed_by_id=actor.id)

    assert client.get(f"/cabinet/students/{student.id}/portfolio").status_code == 404


# ── список пользователей суперадмина ─────────────────────────────────────────

def test_superadmin_users_hides_archived_by_default(superadmin_client, db, student):
    client, actor = superadmin_client
    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)

    resp = client.get("/cabinet/superadmin/users")
    assert "Прошлый" not in resp.text

    resp = client.get("/cabinet/superadmin/users?show_archived=1")
    assert "Прошлый" in resp.text


def test_archived_not_counted_as_blocked(superadmin_client, db, student):
    client, actor = superadmin_client
    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)

    resp = client.get("/cabinet/superadmin/users?show_blocked=1")
    assert "Прошлый" not in resp.text


def test_archive_route_round_trip(superadmin_client, db, student):
    client, _ = superadmin_client
    resp = client.post(f"/cabinet/superadmin/users/{student.id}/archive", follow_redirects=False)
    assert resp.status_code == 303
    db.refresh(student)
    assert student.archived_at is not None

    resp = client.post(f"/cabinet/superadmin/users/{student.id}/unarchive", follow_redirects=False)
    assert resp.status_code == 303
    db.refresh(student)
    assert student.archived_at is None
    assert student.is_active is True


def test_archive_button_visible_to_superadmin(superadmin_client):
    client, _ = superadmin_client
    resp = client.get("/cabinet/students")
    assert "/cabinet/archive" in resp.text


def test_archive_button_hidden_from_admin(client, user_factory, session_factory):
    admin = user_factory(vk_id=910010, name="Админ", role_name="админ")
    sess = session_factory(admin)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet/students")
    assert resp.status_code == 200
    assert "/cabinet/archive" not in resp.text


# ── переписки архивного ученика ──────────────────────────────────────────────

def _mk_cycle_with_final(db, user_id):
    from datetime import date
    from app.models.exam_cycle import ExamCycle
    from app.models.work import Work, WORK_TYPE_MOCK_EXAM

    cycle = ExamCycle(user_id=user_id, subject="Рисунок", started_at=date(2026, 5, 10))
    db.add(cycle)
    db.commit()
    db.refresh(cycle)

    work = Work(
        user_id=user_id,
        work_type=WORK_TYPE_MOCK_EXAM,
        month="май", year=2026,
        filename="final.jpg",
        subject="Рисунок",
        status="success",
        s3_url="https://example.test/final.jpg",
        is_final=True,
        cycle_id=cycle.id,
        attempt_number=1,
    )
    db.add(work)
    db.commit()
    return cycle, work


def test_archived_student_dialogs_stay_readable(superadmin_client, db, student):
    """Ради этого архив и делался: переписки прошлого потока должны открываться."""
    client, actor = superadmin_client
    cycle, _ = _mk_cycle_with_final(db, student.id)
    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)

    # Список циклов ученика (вкладка «Пробники» в архиве)
    resp = client.get(f"/cabinet/students/{student.id}/cycles")
    assert resp.status_code == 200

    # Сам диалог обратной связи
    resp = client.get(f"/cabinet/superadmin/feedback/{cycle.id}")
    assert resp.status_code == 200


def test_archive_mock_exams_tab_links_to_dialog(superadmin_client, db, student):
    client, actor = superadmin_client
    cycle, work = _mk_cycle_with_final(db, student.id)
    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)

    resp = client.get(f"/cabinet/students/{student.id}/mock-exams")
    assert resp.status_code == 200
    # Кнопка «Открыть обратную связь» строится из cycle_id работы
    assert str(cycle.id) in resp.text


def test_archived_cycles_leave_staff_working_list(db, student, user_factory):
    """Тот же цикл не должен висеть в рабочем списке нового потока."""
    from app.api.feedback import _staff_cycles_data

    actor = user_factory(vk_id=910011, name="SA", role_name="суперадмин")
    cycle, _ = _mk_cycle_with_final(db, student.id)
    staff = {"user_id": actor.id, "role_rank": 5}

    ids = [row["id"] for row in _staff_cycles_data(db, staff)]
    assert cycle.id in ids

    archive_user(db, target_user_id=student.id, performed_by_id=actor.id)

    ids = [row["id"] for row in _staff_cycles_data(db, staff)]
    assert cycle.id not in ids


def test_archive_button_on_superadmin_dashboard(superadmin_client):
    """Кнопка должна быть там, куда суперадмин попадает после входа."""
    client, _ = superadmin_client
    resp = client.get("/cabinet/superadmin")
    assert resp.status_code == 200
    assert "/cabinet/archive" in resp.text
