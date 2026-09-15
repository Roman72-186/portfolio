"""Экран «Оценка точки А» — список учеников, плашки, средний балл.

Лиза 14.09.2026 (голосовое + эскиз): вход «Оценка точки А» → ники подряд →
карточка ученика с плашками (пробники, оба портфолио, контрольные) → балл у
каждой → средний балл сам.

Отдельно стерегутся две вещи, которые легко сломать:
- контрольная отличается от пробника только меткой `ExamAssignment.kind`,
  отдельной сущности у неё нет (этим закрыта развилка, висевшая с 27.08.2026);
- промежуточные попытки в плашку не попадают — их балл живёт в
  `ExamCycle.intermediate_score` и в статистику пробников не входит.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.user import User
from app.models.work import Work

LIST_URL = "/cabinet/staff/point-a"
DETAIL_URL = "/cabinet/staff/point-a/{}"
AFTER_SCORE_URL = "/cabinet/staff/point-a/{}/portfolio-after/score"


@pytest.fixture()
def student(user_factory):
    return user_factory(vk_id=880_001, name="Ученик Точкин")


@pytest.fixture()
def admin(user_factory):
    return user_factory(vk_id=880_002, name="Главный преподаватель", role_name="админ")


def _as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def _before_work(db, user_id):
    work = Work(
        user_id=user_id, work_type="before", month="сентябрь", year=2026,
        filename="before.jpg", s3_url="https://s3.example.com/before.jpg",
        status="success",
    )
    db.add(work)
    db.commit()
    return work


def _after_work(db, user_id):
    work = Work(
        user_id=user_id, work_type="after", month="октябрь", year=2026,
        filename="after.jpg", s3_url="https://s3.example.com/after.jpg",
        status="success",
    )
    db.add(work)
    db.commit()
    return work


def _exam_work(
    db, *, user_id, admin_id, kind="mock", subject="Рисунок",
    score=None, is_final=True, seq=1,
):
    """Сданная работа экзаменационного цикла нужного вида и предмета."""
    assignment = ExamAssignment(
        title=f"{kind}-{subject}", subject=subject, kind=kind,
        created_by_id=admin_id, status="published",
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    ticket = ExamTicket(
        assignment_id=assignment.id, ticket_number=seq, title="Билет",
        start_date=date(2026, 9, 1), end_date=date(2026, 9, 30),
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    cycle = ExamCycle(
        user_id=user_id, subject=subject, ticket_id=ticket.id,
        started_at=date(2026, 9, 10),
    )
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    work = Work(
        user_id=user_id, work_type="mock_exam", subject=subject,
        month="сентябрь", year=2026, filename=f"{kind}.jpg",
        s3_url=f"https://s3.example.com/{kind}-{subject}.jpg",
        status="success", cycle_id=cycle.id, is_final=is_final,
        attempt_number=seq, score=score,
    )
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


# ── доступ ──────────────────────────────────────────────────────────────────


def test_head_teacher_opens_the_list(client, session_factory, admin):
    _as(client, session_factory, admin)

    resp = client.get(LIST_URL)

    assert resp.status_code == 200
    assert "Оценка точки А" in resp.text


def test_superadmin_sees_students_in_the_list(
    client, db, user_factory, session_factory, student
):
    """Не только 200: пустой список отдаёт тот же код.

    Список берётся из `_accessible_students`, а та для ранга ≥ 4 ищет учеников
    по `Role.rank == 1`. Стоит этой связке разъехаться — экран молча покажет
    «Учеников пока нет», и отличить это от отказа по коду ответа нельзя.
    """
    boss = user_factory(vk_id=880_003, name="Суперадмин", role_name="суперадмин")
    _before_work(db, student.id)
    _as(client, session_factory, boss)

    resp = client.get(LIST_URL)

    assert resp.status_code == 200
    assert student.name in resp.text
    assert "Учеников пока нет" not in resp.text


def test_head_teacher_sees_students_in_the_list(
    client, db, user_factory, session_factory, admin, student
):
    _before_work(db, student.id)
    _as(client, session_factory, admin)

    resp = client.get(LIST_URL)

    assert student.name in resp.text
    assert "оценено 0 из 1" in resp.text


def test_archived_student_is_not_in_the_list(
    client, db, user_factory, session_factory, admin
):
    """Ученик прошлого потока в очередь входной оценки не попадает."""
    gone = user_factory(vk_id=880_004, name="Архивный Ученик", is_active=False)
    _before_work(db, gone.id)
    _as(client, session_factory, admin)

    assert gone.name not in client.get(LIST_URL).text


def test_list_does_not_load_photos(db, user_factory):
    """На списке фотографии не показываются — и не собираются.

    Сбор «После» тянет работы и сдачи в заданиях из двух таблиц и группирует
    их по месяцам; на каждого ученика в списке это была бы самая дорогая часть
    запроса, показывающая ровно ничего.
    """
    from app.services.point_a import point_a_rows, student_point_a

    student = user_factory(vk_id=880_700, name="Ученик")
    _before_work(db, student.id)
    _after_work(db, student.id)

    row_plates = point_a_rows(db, [student])[0]["point_a"].plates
    detail_plates = student_point_a(db, student).plates

    assert [p.key for p in row_plates] == [p.key for p in detail_plates]  # плашки те же
    assert all(p.images == [] for p in row_plates)
    assert any(p.images for p in detail_plates)


@pytest.mark.parametrize("role_name", ["куратор", "модератор", "ученик"])
def test_everyone_below_head_teacher_is_refused(
    client, db, user_factory, session_factory, student, role_name
):
    """Точку А ставит только Главный преподаватель (владелец 09.09.2026)."""
    staff = user_factory(vk_id=880_100 + len(role_name), name="Сотрудник", role_name=role_name)
    student.curator_id = staff.id
    db.commit()
    _as(client, session_factory, staff)

    assert client.get(LIST_URL).status_code == 403
    assert client.get(DETAIL_URL.format(student.id)).status_code == 403
    assert client.post(AFTER_SCORE_URL.format(student.id), json={"score": 70}).status_code == 403


def test_unknown_student_is_404(client, session_factory, admin):
    _as(client, session_factory, admin)

    assert client.get(DETAIL_URL.format(999_999)).status_code == 404
    assert client.post(AFTER_SCORE_URL.format(999_999), json={"score": 70}).status_code == 404


# ── плашки ──────────────────────────────────────────────────────────────────


def test_student_without_works_has_no_plates(client, session_factory, admin, student):
    """Плашка появляется только под существующие работы: иначе новичок висел
    бы вечным «не оценено» без способа это снять."""
    _as(client, session_factory, admin)

    resp = client.get(DETAIL_URL.format(student.id))

    assert resp.status_code == 200
    assert "Ученик пока ничего не сдал" in resp.text


def test_portfolio_plates_show_up_with_works(client, db, session_factory, admin, student):
    _before_work(db, student.id)
    _after_work(db, student.id)
    _as(client, session_factory, admin)

    resp = client.get(DETAIL_URL.format(student.id))

    assert "Портфолио «До»" in resp.text
    assert "Портфолио «После»" in resp.text
    assert "https://s3.example.com/before.jpg" in resp.text  # оценка не вслепую


def test_mock_and_control_are_separate_plates(client, db, session_factory, admin, student):
    """Вид задания различается меткой `kind` — отдельной сущности у контрольной
    нет и заводить её не нужно."""
    _exam_work(db, user_id=student.id, admin_id=admin.id, kind="mock", subject="Рисунок")
    _exam_work(db, user_id=student.id, admin_id=admin.id, kind="control", subject="Рисунок", seq=2)
    _as(client, session_factory, admin)

    resp = client.get(DETAIL_URL.format(student.id))

    assert "Пробник — рисунок" in resp.text
    assert "Контрольная — рисунок" in resp.text


def test_intermediate_attempt_is_not_a_plate(client, db, session_factory, admin, student):
    """Промежуточная попытка балла точки А не несёт: он живёт в
    `ExamCycle.intermediate_score` и в статистику пробников не входит."""
    _exam_work(
        db, user_id=student.id, admin_id=admin.id,
        kind="mock", subject="Композиция", is_final=False,
    )
    _as(client, session_factory, admin)

    resp = client.get(DETAIL_URL.format(student.id))

    assert "Пробник — композиция" not in resp.text


def test_latest_submission_wins_when_there_are_several_cycles(db, user_factory):
    """Циклов по предмету бывает несколько — в плашку идёт самая свежая сдача."""
    from app.services.point_a import student_point_a

    admin = user_factory(vk_id=880_200, name="ГП", role_name="админ")
    student = user_factory(vk_id=880_201, name="Ученик")
    old = _exam_work(db, user_id=student.id, admin_id=admin.id, score=40, seq=1)
    fresh = _exam_work(db, user_id=student.id, admin_id=admin.id, score=90, seq=2)
    old.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    db.commit()

    plates = student_point_a(db, student).plates

    assert [p.score for p in plates] == [90]
    assert plates[0].work_id == fresh.id


# ── средний балл ────────────────────────────────────────────────────────────


def test_average_waits_for_every_plate(db, user_factory):
    """Средний балл считается по выставленным, но «разобран» ставится только
    когда пустых плашек не осталось."""
    from app.services.point_a import student_point_a

    admin = user_factory(vk_id=880_300, name="ГП", role_name="админ")
    student = user_factory(vk_id=880_301, name="Ученик")
    _exam_work(db, user_id=student.id, admin_id=admin.id, subject="Рисунок", score=80)
    _before_work(db, student.id)

    point_a = student_point_a(db, student)

    assert point_a.average == 80
    assert point_a.is_done is False
    assert point_a.scored_count == 1


def test_average_over_mixed_numeric_types(db, user_factory):
    """`Work.score` приходит `Decimal`, баллы портфолио — `int`. Смешанная
    сумма не должна ни падать, ни считаться по другим правилам округления."""
    from app.services.point_a import student_point_a

    admin = user_factory(vk_id=880_400, name="ГП", role_name="админ")
    student = user_factory(vk_id=880_401, name="Ученик")
    _exam_work(db, user_id=student.id, admin_id=admin.id, subject="Рисунок", score=81)
    _before_work(db, student.id)
    student.portfolio_before_score = 61
    db.commit()

    point_a = student_point_a(db, student)

    assert point_a.average == 71  # (81 + 61) / 2
    assert point_a.is_done is True


def test_average_rounds_like_the_rest_of_the_project(db, user_factory):
    """Ровные половины округляются к чётному — так же, как в
    `stats.py::avg_score_by_subject_all_time` (обычный `round` в Python).

    Зафиксировано тестом, потому что 70.5 → 70 выглядит как ошибка на единицу,
    пока не знаешь правила. Менять его тут в одиночку нельзя: средний балл
    разошёлся бы с баллами на карточке ученика.
    """
    from app.services.point_a import student_point_a

    admin = user_factory(vk_id=880_410, name="ГП", role_name="админ")
    student = user_factory(vk_id=880_411, name="Ученик")
    _exam_work(db, user_id=student.id, admin_id=admin.id, subject="Рисунок", score=81)
    _before_work(db, student.id)
    student.portfolio_before_score = 60
    db.commit()

    assert student_point_a(db, student).average == 70  # (81 + 60) / 2 = 70.5


def test_student_with_nothing_has_no_average(db, user_factory):
    from app.services.point_a import student_point_a

    student = user_factory(vk_id=880_500, name="Пустой ученик")

    point_a = student_point_a(db, student)

    assert point_a.plates == []
    assert point_a.average is None
    assert point_a.is_done is False  # «разобран» без единой плашки не бывает


def test_done_students_sink_to_the_bottom(db, user_factory):
    """Разобранные не прячутся, а уезжают вниз — Лиза просила их видеть."""
    from app.services.point_a import point_a_rows

    done = user_factory(vk_id=880_600, name="Аня Разобранная")
    pending = user_factory(vk_id=880_601, name="Яна Неоценённая")
    _before_work(db, done.id)
    _before_work(db, pending.id)
    done.portfolio_before_score = 75
    db.commit()

    rows = point_a_rows(db, [done, pending])

    assert [row["point_a"].student.id for row in rows] == [pending.id, done.id]
    assert rows[1]["point_a"].is_done is True


# ── балл за портфолио «После» ───────────────────────────────────────────────


def test_head_teacher_scores_the_after_set(client, db, session_factory, admin, student):
    _as(client, session_factory, admin)

    resp = client.post(AFTER_SCORE_URL.format(student.id), json={"score": 88})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "score": 88}
    fresh = db.query(User).filter(User.id == student.id).first()
    assert fresh.portfolio_after_score == 88
    assert fresh.portfolio_after_scored_by_id == admin.id
    assert fresh.portfolio_after_scored_at is not None


@pytest.mark.parametrize("score", [-1, 101, 1000])
def test_after_score_outside_the_scale_is_refused(
    client, db, session_factory, admin, student, score
):
    _as(client, session_factory, admin)

    assert client.post(AFTER_SCORE_URL.format(student.id), json={"score": score}).status_code == 422
    assert db.query(User).filter(User.id == student.id).first().portfolio_after_score is None


def test_after_score_refuses_extra_fields(client, session_factory, admin, student):
    """`extra=forbid`: лишнее поле — опечатка вызывающего, не тихий no-op."""
    _as(client, session_factory, admin)

    resp = client.post(AFTER_SCORE_URL.format(student.id), json={"score": 60, "comment": "ок"})

    assert resp.status_code == 422
