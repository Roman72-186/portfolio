"""Карточка ученика собирает всё про ученика (владелец 05.10.2026).

Шапка как у ученика в «Трекере» (Р/К, точка А, уровень) — всем, кто открыл
карточку, включая куратора; подробности точки А — только ГП и выше. Блок
«Управление» (куратор, тариф, срок, учебные метки, вход, блок, архив) — ГП
и выше, кнопки по тем же действиям `people:*`, что и адреса «Людей», куда
они ходят. Вкладка «Активность» — входы, видео, задания и лента событий.
Карточка ученика в «Людях» уводит сюда.
"""

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.models.activity_event import StudentActivityEvent
from app.models.audit_log import AuditLog
from app.models.learning_topic import LearningTopic
from app.models.section_access import SectionAccessRule
from app.models.task_block import BLOCK_TIMED, TaskBlock, TaskBlockState, TaskBlockSubmission
from app.models.tracker import TrackerTask
from app.services.point_a import PointA
from app.services.tz import msk_input_value
from app.services.user_management import apply_tariff_change, archive_user


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _profile(client, student) -> dict:
    resp = client.get(f"/cabinet/students/{student.id}/profile")
    assert resp.status_code == 200, resp.text
    return resp.json()["student"]


def _fake_point_a(monkeypatch, *, is_done, average):
    from app.api import cabinet_students_shared

    monkeypatch.setattr(
        cabinet_students_shared, "student_point_a",
        lambda db, student, with_images=True: PointA(
            student=student, plates=[object()], average=average, is_done=is_done, scored_count=0,
        ),
    )


@pytest.fixture()
def people(user_factory):
    curator = user_factory(vk_id=960_001, name="Куратор", role_name="куратор")
    chief = user_factory(vk_id=960_002, name="Главный", is_admin=True, role_name="админ")
    superadmin = user_factory(vk_id=960_003, name="Супер", is_admin=True, role_name="суперадмин")
    student = user_factory(vk_id=960_004, name="Ученик", role_name="ученик")
    student.curator_id = curator.id
    return {"curator": curator, "chief": chief, "superadmin": superadmin, "student": student}


# ── Шапка ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("average,level", [(65, 1), (66, 2)])
def test_curator_sees_point_a_and_level_in_hero(client, db, session_factory, people, monkeypatch, average, level):
    db.commit()
    _fake_point_a(monkeypatch, is_done=True, average=average)
    _login(client, session_factory, people["curator"])

    s = _profile(client, people["student"])

    assert s["hero"] == {"point_a_average": average, "point_a_level": level}
    # Экран точки А куратору закрыт — ни «пока N», ни ссылки.
    assert "point_a" not in s["study_now"]
    assert s["manage"] is None


def test_partial_point_a_stays_out_of_hero(client, db, session_factory, people, monkeypatch):
    db.commit()
    _fake_point_a(monkeypatch, is_done=False, average=74)
    _login(client, session_factory, people["chief"])

    s = _profile(client, people["student"])

    assert s["hero"] == {"point_a_average": None, "point_a_level": None}
    assert s["study_now"]["point_a"] == {"has_plates": True, "average": 74, "is_done": False}


# ── Блок «Управление»: кому какие кнопки ─────────────────────────────────────

def test_chief_gets_manage_block_without_archive(client, db, session_factory, people):
    db.commit()
    _login(client, session_factory, people["chief"])

    m = _profile(client, people["student"])["manage"]

    assert m["can_edit"] and m["can_login"] and m["can_impersonate"] and m["can_block"]
    # Архив по умолчанию — только суперадмину (`people:archive`, inherits=False).
    assert m["can_archive"] is False
    assert m["curator_id"] == people["curator"].id
    assert people["curator"].id in [c["id"] for c in m["curators"]]


def test_superadmin_may_archive(client, db, session_factory, people):
    db.commit()
    _login(client, session_factory, people["superadmin"])

    assert _profile(client, people["student"])["manage"]["can_archive"] is True


def test_closed_action_hides_buttons_and_refuses(client, db, session_factory, people):
    chief = people["chief"]
    db.add(SectionAccessRule(section_key="people:students", user_id=chief.id, level="none"))
    db.commit()
    _login(client, session_factory, chief)

    assert _profile(client, people["student"])["manage"]["can_edit"] is False
    resp = client.post(
        f"/cabinet/superadmin/users/{people['student'].id}/labels",
        data={"study_mode": "online"}, headers={"Accept": "application/json"},
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("path", ["labels", "access-until"])
def test_curator_cannot_write(client, db, session_factory, people, path):
    db.commit()
    _login(client, session_factory, people["curator"])

    resp = client.post(
        f"/cabinet/superadmin/users/{people['student'].id}/{path}",
        data={}, headers={"Accept": "application/json"},
    )

    assert resp.status_code == 403


def test_chief_cannot_edit_another_chief(client, db, session_factory, people, user_factory):
    other = user_factory(vk_id=960_010, name="Другой", is_admin=True, role_name="админ")
    db.commit()
    _login(client, session_factory, people["chief"])

    resp = client.post(f"/cabinet/superadmin/users/{other.id}/labels", data={})

    assert resp.status_code == 400  # не ученик — поля нет вовсе


def test_archived_student_is_read_only(client, db, session_factory, people):
    student = people["student"]
    db.commit()
    archive_user(db, target_user_id=student.id, performed_by_id=people["superadmin"].id, actor_rank=5)
    _login(client, session_factory, people["superadmin"])

    resp = client.post(f"/cabinet/superadmin/users/{student.id}/access-until", data={"access_until": ""})
    assert resp.status_code == 409

    resp = client.get(f"/cabinet/students/{student.id}/profile")
    assert resp.status_code == 200
    m = resp.json()["student"]["manage"]
    assert m["is_archived"] and not m["can_edit"] and not m["can_block"] and m["can_archive"]


# ── Запись ──────────────────────────────────────────────────────────────────

def test_labels_saved(client, db, session_factory, people):
    student = people["student"]
    db.commit()
    _login(client, session_factory, people["chief"])

    resp = client.post(f"/cabinet/superadmin/users/{student.id}/labels", data={
        "study_mode": "online", "exam_dates": "15-20 июня", "exam_subjects": "Р + К",
        "is_publishable": "1", "about": "Любит акварель",
    })

    assert resp.status_code == 200, resp.text
    db.refresh(student)
    assert (student.study_mode, student.exam_dates, student.exam_subjects) == ("online", "15-20 июня", "Р + К")
    assert student.is_publishable is True
    assert student.about == "Любит акварель"


def test_access_until_set_clear_and_garbage(client, db, session_factory, people):
    student = people["student"]
    db.commit()
    _login(client, session_factory, people["chief"])
    url = f"/cabinet/superadmin/users/{student.id}/access-until"

    assert client.post(url, data={"access_until": "2026-10-27T23:30"}).status_code == 200
    db.refresh(student)
    assert msk_input_value(student.access_until) == "2026-10-27T23:30"

    assert client.post(url, data={"access_until": "не дата"}).status_code == 400
    db.refresh(student)
    assert student.access_until is not None

    assert client.post(url, data={"access_until": ""}).status_code == 200
    db.refresh(student)
    assert student.access_until is None
    actions = [a.action for a in db.query(AuditLog).filter(AuditLog.target_user_id == student.id)]
    assert actions.count("access_until_change") == 2


def test_access_until_marks_newcomer_without_tariff(client, db, session_factory, people, user_factory):
    """Срок у незаполнившего анкету — новичок пробного набора: тариф из
    дефолта аккаунта снимается, как на входе по ссылке `/proba`."""
    newcomer = user_factory(vk_id=960_020, name="Новичок", role_name="ученик", profile_completed=False)
    assert newcomer.tariff
    db.commit()
    _login(client, session_factory, people["chief"])

    resp = client.post(
        f"/cabinet/superadmin/users/{newcomer.id}/access-until", data={"access_until": "2026-10-27T23:30"},
    )

    assert resp.status_code == 200
    assert resp.json()["tariff"] == ""


def test_anketa_only_leaves_tariff_cohort_and_access(client, db, session_factory, people):
    student = people["student"]
    student.cohort_tag = "may"
    student.access_until = datetime.now(timezone.utc) + timedelta(days=3)
    tariff = student.tariff
    db.commit()
    before = student.access_until
    _login(client, session_factory, people["chief"])

    resp = client.post(f"/cabinet/students/{student.id}/profile", data={
        "first_name": "Иван", "last_name": "Петров", "phone": "+79990000000", "anketa_only": "1",
    })

    assert resp.status_code == 200, resp.text
    db.refresh(student)
    assert (student.first_name, student.tariff, student.cohort_tag) == ("Иван", tariff, "may")
    assert student.access_until == before


def test_credentials_and_link_answer_json(client, db, session_factory, people):
    student = people["student"]
    db.commit()
    _login(client, session_factory, people["chief"])
    headers = {"Accept": "application/json"}

    creds = client.post(f"/cabinet/superadmin/users/{student.id}/set-credentials", headers=headers)
    assert creds.status_code == 200
    body = creds.json()
    assert body["ok"] and body["login"] and body["password"]

    link = client.post(f"/cabinet/superadmin/users/{student.id}/issue-link", headers=headers)
    assert link.status_code == 200
    assert link.json()["link"]


def test_card_rerenders_after_issuing_password():
    """Проход 06.10.2026: выдача пароля стирала кэш карточки и не
    перерисовывала её — до перезагрузки «Логин и пароль не выдавались»,
    повторная выдача падала, «Редактировать анкету» не открывалась."""
    source = (pathlib.Path(__file__).resolve().parents[1] / "app/static/js/cabinet_students.js").read_text(encoding="utf-8")
    body = source.split("function issueCredentials()", 1)[1].split("\nfunction ", 1)[0]

    assert "reloadProfile();" in body
    assert "_tabCache[_currentStudentId] = {};" not in body


# ── Строка списка после правки ──────────────────────────────────────────────

def _list_row(page: str, student_id: int) -> str:
    start = page.rindex("<button", 0, page.index(f'id="srow-{student_id}"'))
    end = page.index("</button>", start) + len("</button>")
    return " ".join(page[start:end].split())


def test_row_shows_fresh_tariff_cohort_and_name(client, db, session_factory, people):
    """Проход 06.10.2026, пункт 3: после смены тарифа, метки набора и имени
    строка списка до перезагрузки оставалась старой, а фильтр по тарифу уже
    работал по новому. Карточка перечитывает строку у сервера."""
    student = people["student"]
    student.profile_completed = True
    db.commit()
    _login(client, session_factory, people["chief"])

    assert client.post(f"/cabinet/superadmin/users/{student.id}/tariff", data={"tariff": "Я С ВАМИ"}).status_code == 200
    assert client.post(f"/cabinet/superadmin/users/{student.id}/cohort-tag", data={"cohort_tag": "may"}).status_code == 200
    assert client.post(f"/cabinet/students/{student.id}/profile", data={
        "first_name": "Ева", "last_name": "Новикова", "phone": "+79990000000", "anketa_only": "1",
    }).status_code == 200

    resp = client.get(f"/cabinet/students/{student.id}/row")

    assert resp.status_code == 200, resp.text
    row = " ".join(resp.text.split())
    assert 'data-tariff="Я С ВАМИ"' in row
    assert ">Я с вами<" in row
    assert 'cohort-badge cohort-may">М<' in row
    assert ">Новикова Ева<" in row
    # Разметка одна — та же, что у строки в самом списке.
    assert row == _list_row(client.get("/cabinet/students").text, student.id)


def test_curator_row_only_own(client, db, session_factory, people, user_factory):
    stranger = user_factory(vk_id=960_031, name="Чужой", role_name="ученик")
    db.commit()
    _login(client, session_factory, people["curator"])

    assert client.get(f"/cabinet/students/{people['student'].id}/row").status_code == 200
    assert client.get(f"/cabinet/students/{stranger.id}/row").status_code == 403


def test_card_refreshes_list_row_after_saving():
    source = (pathlib.Path(__file__).resolve().parents[1] / "app/static/js/cabinet_students.js").read_text(encoding="utf-8")
    reload_body = source.split("function reloadProfile(", 1)[1].split("\nfunction ", 1)[0]
    anketa_body = source.split("function saveProfile()", 1)[1].split("\nfunction ", 1)[0]

    assert "refreshSidebarRow(id);" in reload_body
    assert "refreshSidebarRow(_currentStudentId);" in anketa_body


# ── Архив только читают ─────────────────────────────────────────────────────

def test_archive_view_hides_portfolio_month_buttons(client, db, session_factory, people):
    """Суперадмин видел в архиве «Переименовать», крестики и перетаскивание,
    а сервер отвечал на них 404 «Not found» (проход 06.10.2026)."""
    student = people["student"]
    db.commit()
    archive_user(db, target_user_id=student.id, performed_by_id=people["superadmin"].id, actor_rank=5)
    _login(client, session_factory, people["superadmin"])

    archive_page = client.get(f"/cabinet/archive?student={student.id}").text
    active_page = client.get("/cabinet/students").text

    assert "const CAN_PORTFOLIO_MONTHS = false;" in archive_page
    assert "const CAN_PORTFOLIO_MONTHS = true;" in active_page


# ── «Люди» уводят в «Учеников» ───────────────────────────────────────────────

def test_people_card_of_student_redirects(client, db, session_factory, people):
    db.commit()
    _login(client, session_factory, people["chief"])

    resp = client.get(f"/cabinet/superadmin/users/{people['student'].id}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"/cabinet/students?student={people['student'].id}"

    # Карточка сотрудника остаётся на месте.
    staff = client.get(f"/cabinet/superadmin/users/{people['curator'].id}", follow_redirects=False)
    assert staff.status_code == 200


def test_blocked_student_keeps_people_card(client, db, session_factory, people):
    """Заблокированного «Ученики» не открывают — остаётся старая карточка."""
    people["student"].is_active = False
    db.commit()
    _login(client, session_factory, people["chief"])

    resp = client.get(f"/cabinet/superadmin/users/{people['student'].id}", follow_redirects=False)

    assert resp.status_code == 200


# ── Вкладка «Статистика» (с 06.10.2026 в ней и бывшая «Активность») ────────

def _statistics(client, student) -> dict:
    resp = client.get(f"/cabinet/students/{student.id}/statistics")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _activity(client, student):
    return _statistics(client, student)["activity"]


def test_activity_counts_and_feed(client, db, session_factory, people):
    student = people["student"]
    db.add_all([
        StudentActivityEvent(user_id=student.id, event_type="login"),
        StudentActivityEvent(user_id=student.id, event_type="login"),
        StudentActivityEvent(user_id=student.id, event_type="work_upload", details="2 файл(ов)"),
    ])
    apply_tariff_change(db, people["chief"].id, student, "Я САМ")
    db.commit()

    _login(client, session_factory, people["chief"])
    a = _activity(client, student)
    assert (a["logins"], a["uploads"]) == (2, 1)
    staff = [e for e in a["feed"] if e["kind"] == "staff"]
    assert staff and staff[0]["label"] == "Смена тарифа"
    assert "Главный" in staff[0]["by"]

    # Куратор видит, что делал ученик, но не кто из сотрудников его правил.
    _login(client, session_factory, people["curator"])
    a = _activity(client, student)
    assert a["feed"] and all(e["kind"] == "student" for e in a["feed"])


def test_curator_activity_only_own(client, db, session_factory, people, user_factory):
    stranger = user_factory(vk_id=960_030, name="Чужой", role_name="ученик")
    db.commit()
    _login(client, session_factory, people["curator"])

    assert client.get(f"/cabinet/students/{stranger.id}/statistics").status_code == 403


def test_activity_queries_do_not_grow_with_history(client, db, session_factory, people, sql_counter):
    student = people["student"]
    # По одной записи каждого журнала заранее: запрос имён сотрудников идёт,
    # только когда их действия есть в ленте, — это разовый, а не растущий.
    db.add(StudentActivityEvent(user_id=student.id, event_type="login"))
    apply_tariff_change(db, people["chief"].id, student, "Я С ВАМИ")
    db.commit()
    _login(client, session_factory, people["chief"])

    def _count():
        with sql_counter() as c:
            _activity(client, student)
        return c.count

    _activity(client, student)  # прогрев кэшей сессии
    before = _count()
    db.add_all([StudentActivityEvent(user_id=student.id, event_type="login") for _ in range(30)])
    for tariff in ("Я САМ", "Я С ВАМИ", "Я САМ"):
        apply_tariff_change(db, people["chief"].id, student, tariff)
    db.commit()

    assert _count() == before


def _late_control(db, student, scorer):
    """Контрольная на время, сданная после срока и оценённая ГП."""
    deadline = datetime.now(timezone.utc) - timedelta(days=1)
    topic = LearningTopic(title="Цикл", kind="week", opens_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                          is_published=True, assign_to_all=True)
    db.add(topic)
    db.flush()
    task = TrackerTask(title="Контрольная по рисунку", topic_id=topic.id, kind="material",
                       is_published=True, submit_until=deadline, subject="Рисунок")
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type=BLOCK_TIMED, title="Локация с дверью",
                      time_limit_minutes=75)
    db.add(block)
    db.flush()
    db.add(TaskBlockState(block_id=block.id, user_id=student.id, status="done",
                          started_at=deadline + timedelta(hours=1),
                          completed_at=deadline + timedelta(hours=2)))
    db.add(TaskBlockSubmission(block_id=block.id, user_id=student.id,
                               submitted_at=deadline + timedelta(hours=2),
                               score=55, scored_by_id=scorer.id))
    db.commit()
    return block


def test_statistics_collects_school_sections_for_chief(client, db, session_factory, people):
    """Владелец 06.10.2026: «собрать туда всё, что касается ребёнка из вкладки
    статистика». Цифры — те же функции, что у «Статистики активности»."""
    chief = people["chief"]
    chief.last_name, chief.first_name = "Иванова", "Анна"
    _late_control(db, people["student"], chief)
    _login(client, session_factory, chief)

    data = _statistics(client, people["student"])

    school = data["school"]
    [row] = school["assignments"]
    assert row["label"] == "Рисунок: Контрольная по рисунку · Локация с дверью"
    assert (row["status"], row["note"]) == ("late", "Сдал после срока")
    assert (row["score"], row["scorer"]) == (55, "Иванова Анна")
    assert (school["deadlines"]["on_time"], school["deadlines"]["late"]) == (0, 1)
    [timed] = school["timed"]
    assert (timed["minutes"], timed["limit"], timed["late"]) == (60, 75, True)
    assert school["diagnostics"] == [] and school["videos"] == []
    assert "points" not in data  # график пробников снят
    assert "activity" in data


def test_statistics_timed_row_for_student_even_in_time(db, user_factory):
    """Дашборд перечисляет только превысивших и опоздавших, карточка — каждую
    контрольную ученика: «уложился» тоже ответ."""
    from app.services.activity_stats import get_timed_stats

    student = user_factory(vk_id=960_040, name="Ученик", role_name="ученик")
    task = TrackerTask(title="Контрольная", kind="material", is_published=True)
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type=BLOCK_TIMED, title="Куб", time_limit_minutes=60)
    db.add(block)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add(TaskBlockState(block_id=block.id, user_id=student.id, status="done",
                          started_at=now - timedelta(minutes=40), completed_at=now))
    db.commit()

    assert get_timed_stats(db)["students"] == []
    [row] = get_timed_stats(db, student_id=student.id)["students"]
    assert (row["minutes"], row["overrun"], row["late"]) == (40, False, False)


def test_curator_statistics_without_school_sections(client, db, session_factory, people):
    """Владелец 06.10.2026: куратору — «как сейчас», то есть бывшая
    «Активность»; разделы «Статистики активности» — с ранга 4."""
    _late_control(db, people["student"], people["chief"])
    _login(client, session_factory, people["curator"])

    data = _statistics(client, people["student"])

    assert data["school"] is None
    assert {"logins", "video", "tasks", "feed"} <= set(data["activity"])


def test_archived_student_statistics_readable(client, db, session_factory, people):
    student = people["student"]
    db.commit()
    archive_user(db, target_user_id=student.id, performed_by_id=people["superadmin"].id, actor_rank=5)
    _login(client, session_factory, people["chief"])

    data = _statistics(client, student)

    # Архивного ученика программа не числит — заданий к сдаче у него нет.
    assert data["school"]["assignments"] == []


def test_old_activity_tab_opens_statistics(client, db, session_factory, people):
    """«Активность» слита в «Статистику» — закладки `?tab=activity` открывают её."""
    db.commit()
    _login(client, session_factory, people["chief"])

    page = client.get(f"/cabinet/students?student={people['student'].id}&tab=activity").text

    assert 'const INITIAL_TAB    = "statistics";' in page
    assert 'id="tab-activity"' not in page
    assert client.get(f"/cabinet/students/{people['student'].id}/activity").status_code == 404
