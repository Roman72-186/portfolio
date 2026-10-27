"""«Был на занятии» → запись занятия необязательна (Александрия 27.10.2026).

«У ребят перед занятием появляется кнопка „подключиться к занятию“. Те, кто
нажал на кнопку, — у них помечается „был на занятии“, и тогда видео с занятия
им становится необязательным к просмотру. А тем, кто не нажимал, запись
занятия становится обязательной.» Владелец 27.10.2026: отметку «был / не был»
вручную ставят ГП и суперадмин, она перекрывает нажатие.

Правило — `task_blocks.required_by_block_for_task` (флаг видео
`optional_if_attended`) и `meeting_joins.attended_link_ids`.
"""
from datetime import timedelta, timezone

from app.models.activity_event import StudentActivityEvent
from app.models.audit_log import AuditLog
from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.learning_video import LearningVideo
from app.models.task_block import (
    BLOCK_LINK,
    BLOCK_UPLOAD,
    BLOCK_VIDEO,
    LessonAttendanceMark,
    TaskBlock,
    TaskBlockState,
)
from app.models.tracker import TrackerTaskState
from app.services.activity_stats import student_activity
from app.services.cycle_feed import build_cycle_feed
from app.services.meeting_joins import EVENT_MEETING_JOIN
from app.services.program import day_bounds
from app.services.staff_dashboard import get_student_activity_overview
from app.services.task_blocks import required_steps_for_student, sync_blocks
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=2)
CYCLE_END = TODAY + timedelta(days=12)
ZOOM = "https://zoom.us/j/1"


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _lesson(db, owner, *, flag=True, link_above=True):
    """Задание «Занятие»: кнопка Zoom → запись занятия → загрузка работ."""
    topic = LearningTopic(
        title="Цикл", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    task = create_task(
        db, title="Занятие 18.10", user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    link = TaskBlock(
        task_id=task.id, block_type=BLOCK_LINK, title="Подключение к занятию",
        url=ZOOM, sort_order=0 if link_above else 3,
    )
    video = TaskBlock(
        task_id=task.id, block_type=BLOCK_VIDEO, title="Запись занятия",
        sort_order=1, optional_if_attended=flag,
    )
    upload = TaskBlock(
        task_id=task.id, block_type=BLOCK_UPLOAD, title="Доработка с занятия",
        sort_order=2, is_required=True,
    )
    db.add_all([link, video, upload])
    db.commit()
    return task, link, video, upload


def _statuses(db, student):
    steps = build_cycle_feed(
        db, user_id=student.id, user_tariff=student.tariff, start=CYCLE_START, end=CYCLE_END,
    )
    return {s["block"].id: s["status"] for s in steps if s["block"] is not None}


def _press(db, student, link):
    db.add(StudentActivityEvent(
        user_id=student.id, event_type=EVENT_MEETING_JOIN, details=f"block:{link.id}",
    ))
    db.commit()


def _required_ids(db, student, task):
    return {
        b.id for b in required_steps_for_student(
            db, task_id=task.id, user_id=student.id, user_tariff=student.tariff,
        )
    }


# ── правило обязательности ──────────────────────────────────────────────────

def test_recording_is_required_for_who_did_not_attend(db, user_factory):
    student = user_factory(vk_id=880_001, name="Не был", role_name="ученик")
    task, _link, video, upload = _lesson(db, student)

    assert _statuses(db, student)[upload.id] == "locked"
    assert video.id in _required_ids(db, student, task)


def test_recording_is_optional_for_who_pressed_the_button(db, user_factory):
    student = user_factory(vk_id=880_002, name="Был", role_name="ученик")
    task, link, video, upload = _lesson(db, student)
    _press(db, student, link)

    assert _statuses(db, student)[upload.id] == "current"
    assert video.id not in _required_ids(db, student, task)


def test_staff_marks_override_the_button_both_ways(db, user_factory):
    """Зашёл в Zoom мимо кнопки — «был»; нажал и не пришёл — «не был»."""
    absent_pressed = user_factory(vk_id=880_003, name="Нажал, не пришёл", role_name="ученик")
    present_silent = user_factory(vk_id=880_004, name="Пришёл мимо кнопки", role_name="ученик")
    task, link, video, _upload = _lesson(db, absent_pressed)
    _press(db, absent_pressed, link)
    db.add_all([
        LessonAttendanceMark(block_id=link.id, user_id=absent_pressed.id, attended=False),
        LessonAttendanceMark(block_id=link.id, user_id=present_silent.id, attended=True),
    ])
    db.commit()

    assert video.id in _required_ids(db, absent_pressed, task)
    assert video.id not in _required_ids(db, present_silent, task)


def test_flag_without_link_above_changes_nothing(db, user_factory):
    """Кнопка «Ссылка» ниже записи — флаг не действует, решает обычная галочка."""
    student = user_factory(vk_id=880_005, name="Ученик", role_name="ученик")
    task, link, video, _upload = _lesson(db, student, link_above=False)
    _press(db, student, link)

    assert video.id not in _required_ids(db, student, task)


def test_pressing_closes_task_when_recording_was_the_last_step(auth_client, db):
    """Загрузка уже сдана, держит только запись: переход по кнопке закрывает
    задание сразу, а не при следующем шаге ученика."""
    client, student = auth_client
    task, link, _video, upload = _lesson(db, student)
    db.add(TaskBlockState(block_id=upload.id, user_id=student.id, status="done"))
    db.commit()

    resp = client.get(f"/cabinet/tracker/blocks/{link.id}/go", follow_redirects=False)

    assert resp.status_code == 302
    state = db.query(TrackerTaskState).filter_by(task_id=task.id, user_id=student.id).one_or_none()
    assert state is not None and state.status == "done"


def test_flag_is_saved_only_on_video(db, user_factory):
    owner = user_factory(vk_id=880_006, name="ГП", role_name="админ", is_admin=True)
    task, link, video, upload = _lesson(db, owner, flag=False)
    clip = LearningVideo(bunny_library_id=1, bunny_video_id="vid-lesson", title="Запись")
    db.add(clip)
    db.commit()

    sync_blocks(db, task_id=task.id, items=[
        {"id": link.id, "block_type": BLOCK_LINK, "title": "Подключение", "url": ZOOM,
         "optional_if_attended": True},
        {"id": video.id, "block_type": BLOCK_VIDEO, "title": "Запись", "video_id": clip.id,
         "optional_if_attended": True},
        {"id": upload.id, "block_type": BLOCK_UPLOAD, "title": "Доработка",
         "body": "Загрузи работу", "optional_if_attended": True},
    ])
    db.commit()
    for block in (link, video, upload):
        db.refresh(block)

    assert (link.optional_if_attended, video.optional_if_attended, upload.optional_if_attended) == (
        False, True, False,
    )


# ── статистика ──────────────────────────────────────────────────────────────

def test_theory_does_not_ask_attendee_to_watch_recording(db, user_factory):
    was = user_factory(vk_id=880_007, name="Был", role_name="ученик")
    missed = user_factory(vk_id=880_008, name="Не был", role_name="ученик")
    _task, link, video, _upload = _lesson(db, was)
    clip = LearningVideo(bunny_library_id=1, bunny_video_id="vid-theory", title="Запись занятия")
    db.add(clip)
    db.commit()
    video.video_id = clip.id
    db.commit()
    _press(db, was, link)

    overview = get_student_activity_overview(db, include_assignments=True)
    [recording] = [item for item in overview["videos"] if item["id"] == video.id]
    [lesson] = [item for item in overview["meetings"] if item["id"] == link.id]

    assert recording["eligible"] == [missed.id]
    assert lesson["submitted"] == [was.id]
    assert lesson["pending"] == [missed.id]


# ── ручная отметка ──────────────────────────────────────────────────────────

def _client_as(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def test_chief_marks_attendance_and_it_is_logged(client, db, user_factory, session_factory):
    chief = user_factory(vk_id=880_010, name="Главный", is_admin=True, role_name="админ")
    student = user_factory(vk_id=880_011, name="Ученик", role_name="ученик")
    task, link, video, _upload = _lesson(db, chief)
    _client_as(client, session_factory, chief)

    resp = client.post(
        f"/cabinet/students/{student.id}/lesson-links/{link.id}/attendance",
        json={"attended": True},
    )

    assert resp.status_code == 200, resp.text
    assert video.id not in _required_ids(db, student, task)
    log = db.query(AuditLog).filter_by(action="lesson_attendance", target_user_id=student.id).one()
    assert log.performed_by_id == chief.id
    assert log.details == "Был на занятии: Подключение к занятию · Занятие 18.10"
    [join] = student_activity(db, student, today=TODAY)["meetings"]["joins"]
    assert join == {"event": "Подключение к занятию · Занятие 18.10", "by_staff": True}
    [item] = [i for i in get_student_activity_overview(db, include_assignments=True)["meetings"]
              if i["id"] == link.id]
    assert item["notes"][student.id] == "Был на занятии (отметил преподаватель)"

    resp = client.post(
        f"/cabinet/students/{student.id}/lesson-links/{link.id}/attendance",
        json={"attended": False},
    )
    assert resp.status_code == 200
    assert video.id in _required_ids(db, student, task)


def test_curator_cannot_mark_attendance(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=880_012, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=880_013, name="Ученик", role_name="ученик")
    student.curator_id = curator.id
    db.commit()
    _task, link, _video, _upload = _lesson(db, curator)
    _client_as(client, session_factory, curator)

    resp = client.post(
        f"/cabinet/students/{student.id}/lesson-links/{link.id}/attendance",
        json={"attended": True},
    )

    assert resp.status_code == 403
    assert db.query(LessonAttendanceMark).count() == 0


def test_mark_only_for_link_block(client, db, user_factory, session_factory):
    chief = user_factory(vk_id=880_014, name="Главный", is_admin=True, role_name="админ")
    student = user_factory(vk_id=880_015, name="Ученик", role_name="ученик")
    _task, _link, video, _upload = _lesson(db, chief)
    _client_as(client, session_factory, chief)

    resp = client.post(
        f"/cabinet/students/{student.id}/lesson-links/{video.id}/attendance",
        json={"attended": True},
    )

    assert resp.status_code == 404


def test_activity_page_shows_mark_button_only_to_senior(client, db, user_factory, session_factory):
    chief = user_factory(vk_id=880_016, name="Главный", is_admin=True, role_name="админ")
    user_factory(vk_id=880_017, name="Ученик", role_name="ученик")
    _lesson(db, chief)
    _client_as(client, session_factory, chief)

    html = client.get("/cabinet/superadmin/activity").text

    assert "const canMarkAttendance = true;" in html


def test_card_lists_pressed_and_staff_marked_lessons(db, user_factory):
    """Нажатие одного занятия и отметка преподавателя другого — оба в списке."""
    student = user_factory(vk_id=880_018, name="Ученик", role_name="ученик")
    _task, first, _video, _upload = _lesson(db, student)
    _task2, second, _video2, _upload2 = _lesson(db, student)
    _press(db, student, first)
    db.add(LessonAttendanceMark(block_id=second.id, user_id=student.id, attended=True))
    db.commit()

    joins = student_activity(db, student, today=TODAY)["meetings"]["joins"]

    assert sorted(j["by_staff"] for j in joins) == [False, True]
