"""Доступ к циклу по тарифам «с – по», срок цикла и архив по этапу.

Владелец 06.10.2026: «нужно настроить доступность циклов для тарифов и указать,
с какой даты по какое доступен ему данный цикл». Цикл идёт в свои общие даты;
до «с» цикла у тарифа нет. Второй заход в тот же день: «по» — срок цикла, а не
архив — должник стоит на цикле, сданное позже пишется «после срока», ему
приходят напоминания; в архив уходит этап целиком, будущие циклы этапа видны
закрытыми.
"""
from datetime import date, datetime, timedelta, timezone

from app.models.learning_topic import (
    TOPIC_KIND_STAGE,
    TOPIC_KIND_WEEK,
    LearningTopic,
    LearningTopicTariff,
)
from app.models.notification import Notification
from app.models.task_block import TaskBlock, TaskBlockState
from app.models.tracker import STATUS_DONE, TrackerTask, TrackerTaskState
from app.services.activity_stats import get_deadline_stats
from app.services.student_reminders import run_student_reminders
from app.services.task_blocks import completed_after_deadline
from app.services.cycle_feed import (
    archive_cycle_ids,
    cycle_is_archived_for_user,
    feed_for_student,
)
from app.services.cycle_stats import cycle_debtors, cycle_stats
from app.services.tracker import cycle_deadline_lookup, cycle_debt, effective_cycle
from app.services.tz import msk_midnight, today_msk
from app.services.video_topics import (
    accessible_topic_ids,
    set_topic_tariff_windows,
    topic_audience_user_ids,
)

from tests.test_program_access_from import _topic, _undated_task, _utc

TODAY = today_msk()
CYCLES_PAGE = "/cabinet/staff/program/cycles"


def _day_start(day):
    return _utc(msk_midnight(day))


def _day_end(day):
    return _utc(msk_midnight(day) + timedelta(hours=23, minutes=59, seconds=59))


def _student(db, user, tariff):
    user.tariff = tariff
    db.commit()
    return user


def _running_cycle(db, owner, title="Цикл"):
    cycle = _topic(
        db, owner, title=title,
        starts_on=TODAY - timedelta(days=3), ends_on=TODAY + timedelta(days=10),
    )
    _undated_task(db, owner, cycle, title=f"Задание: {title}")
    return cycle


# ── видимость ───────────────────────────────────────────────────────────────

def test_cycle_without_tariff_rule_is_open_to_everyone(db, regular_user):
    _student(db, regular_user, "Я САМ")
    cycle = _running_cycle(db, regular_user)

    assert cycle.id in accessible_topic_ids(db, regular_user.id)


def test_unchecked_tariff_does_not_see_the_cycle(db, regular_user):
    _student(db, regular_user, "Я САМ")
    cycle = _running_cycle(db, regular_user)
    set_topic_tariff_windows(db, cycle, {"Я С ВАМИ": (None, None)})
    db.commit()

    assert cycle.id not in accessible_topic_ids(db, regular_user.id)


def test_tariff_sees_the_cycle_only_from_its_start_date(db, regular_user):
    _student(db, regular_user, "Я САМ")
    cycle = _running_cycle(db, regular_user)
    set_topic_tariff_windows(
        db, cycle, {"Я САМ": (_day_start(TODAY + timedelta(days=2)), None)}
    )
    db.commit()

    assert cycle.id not in accessible_topic_ids(db, regular_user.id)
    assert regular_user.id not in topic_audience_user_ids(db, cycle.id)

    set_topic_tariff_windows(db, cycle, {"Я САМ": (_day_start(TODAY), None)})
    db.commit()

    assert cycle.id in accessible_topic_ids(db, regular_user.id)
    assert regular_user.id in topic_audience_user_ids(db, cycle.id)


# ── «по» — срок цикла, а не архив ───────────────────────────────────────────

def _past_deadline_for_self(db, owner):
    """Цикл идёт по общим датам, а срок «Я САМ» прошёл вчера; следом —
    второй цикл. Задание первого цикла — со сдачей работы."""
    closed = _running_cycle(db, owner, title="Цикл 1")
    set_topic_tariff_windows(db, closed, {
        "Я САМ": (None, _day_end(TODAY - timedelta(days=1))),
        "Я С ВАМИ": (None, None),
    })
    following = _topic(
        db, owner, title="Цикл 2",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=10),
    )
    _undated_task(db, owner, following, title="Задание: Цикл 2")
    db.commit()
    return closed, following


def test_tariff_end_date_is_a_deadline_debt_still_holds(db, regular_user):
    """Владелец 06.10.2026: «цикл не нужно отправлять в архив» — должник стоит
    на цикле и после «по», следующие заперты, сдать можно."""
    _student(db, regular_user, "Я САМ")
    closed, following = _past_deadline_for_self(db, regular_user)

    assert effective_cycle(db, regular_user.id, TODAY).id == closed.id
    debt = cycle_debt(db, regular_user.id, TODAY)
    assert debt is not None and debt["cycle"].id == closed.id
    assert following.id in {c.id for c in debt["locked"]}
    assert not cycle_is_archived_for_user(db, regular_user.id, closed.id, TODAY)
    assert closed.id not in archive_cycle_ids(
        db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY,
    )


def test_cycle_deadline_is_tariff_end_or_cycle_end(db, regular_user):
    closed, _ = _past_deadline_for_self(db, regular_user)
    lookup = cycle_deadline_lookup(db, {closed.id})

    assert _utc(lookup(closed.id, "я сам")) == _day_end(TODAY - timedelta(days=1))
    # Без «по» — конец цикла: граница суток после последнего дня, как всегда
    # считала статистика сроков (`day_bounds`).
    assert _utc(lookup(closed.id, "Я С ВАМИ")) == _day_start(TODAY + timedelta(days=11))
    assert lookup(None, "Я САМ") is None


def _handed_in(db, owner, cycle, student, *, title="Сдача работы"):
    task = _undated_task(db, owner, cycle, title=title)
    block = TaskBlock(task_id=task.id, block_type="photo_upload", title="Работа")
    db.add(block)
    db.commit()
    db.add(TaskBlockState(
        block_id=block.id, user_id=student.id, status=STATUS_DONE,
        completed_at=datetime.now(timezone.utc),
    ))
    db.commit()
    return task, block


def test_work_after_tariff_deadline_is_late_in_stats(db, regular_user, user_factory):
    """«Записывать, что работа сдана была после дедлайна… в статистику
    задания и в общую статистику»."""
    self_student = _student(db, regular_user, "Я САМ")
    with_you = user_factory(vk_id=100_201, name="С вами", tariff="Я С ВАМИ")
    closed, _ = _past_deadline_for_self(db, regular_user)
    task, block = _handed_in(db, regular_user, closed, self_student)
    db.add(TaskBlockState(
        block_id=block.id, user_id=with_you.id, status=STATUS_DONE,
        completed_at=datetime.now(timezone.utc),
    ))
    db.commit()

    stats = get_deadline_stats(db)
    by_name = {row["name"]: row for row in stats["students"]}
    assert by_name[self_student.name]["late"] == 1
    assert by_name["С вами"]["on_time"] == 1

    lookup = cycle_deadline_lookup(db, {closed.id})
    state = db.query(TaskBlockState).filter_by(block_id=block.id, user_id=self_student.id).one()
    assert completed_after_deadline(
        block, task, state, user_tariff="Я САМ",
        cycle_deadline=lookup(closed.id, "Я САМ"),
    )
    # Без срока цикла (экраны ученика) — как раньше, отметки нет.
    assert not completed_after_deadline(block, task, state, user_tariff="Я САМ")


def test_debtors_after_tariff_deadline_stay_in_the_list(db, regular_user, user_factory):
    self_student = _student(db, regular_user, "Я САМ")
    with_you = user_factory(vk_id=100_101, name="С вами", tariff="Я С ВАМИ")
    max_student = user_factory(vk_id=100_102, name="Максимум", tariff="УВЕРЕННЫЙ МАКСИМУМ")
    closed, _ = _past_deadline_for_self(db, regular_user)

    stats = cycle_stats(db, closed)
    assert stats["students_by_tariff"]["УВЕРЕННЫЙ МАКСИМУМ"] == 0
    assert stats["students_by_tariff"]["Я САМ"] == 1
    assert stats["students_by_tariff"]["Я С ВАМИ"] == 1

    debtor_ids = {row["user"].id for row in cycle_debtors(db, closed)}
    assert {self_student.id, with_you.id} <= debtor_ids
    assert max_student.id not in debtor_ids


# ── напоминания должнику ────────────────────────────────────────────────────

def _msk(day, hour, minute=0):
    return msk_midnight(day).astimezone(timezone.utc) + timedelta(hours=hour, minutes=minute)


def _cycle_notes(db, user):
    return [
        n for n in db.query(Notification).filter(Notification.user_id == user.id).all()
        if "закрывается" in n.title or "не закрыт" in n.title
    ]


def test_debtor_is_warned_three_hours_before_tariff_deadline(db, regular_user, user_factory):
    student = user_factory(vk_id=100_301, name="Должник", tariff="Я САМ")
    cycle = _running_cycle(db, regular_user, title="Цикл 1")
    deadline = _msk(TODAY + timedelta(days=1), 18)
    set_topic_tariff_windows(db, cycle, {"Я САМ": (None, deadline)})
    db.commit()

    run_student_reminders(db, now=deadline - timedelta(hours=4))
    assert _cycle_notes(db, student) == []

    run_student_reminders(db, now=deadline - timedelta(hours=2))
    notes = _cycle_notes(db, student)
    assert len(notes) == 1
    assert notes[0].title == "Цикл 1 закрывается через 3 часа"
    assert "Задание: Цикл 1" in notes[0].text

    run_student_reminders(db, now=deadline - timedelta(hours=1))
    assert len(_cycle_notes(db, student)) == 1


def test_debtor_is_reminded_daily_after_deadline(db, regular_user, user_factory):
    student = user_factory(vk_id=100_302, name="Должник", tariff="Я САМ")
    cycle = _running_cycle(db, regular_user, title="Цикл 1")
    # Срок — после включения рассылки (`CYCLE_DEBT_REMINDERS_SINCE`).
    day = max(TODAY, date(2026, 10, 8))
    set_topic_tariff_windows(db, cycle, {"Я САМ": (None, _day_end(day - timedelta(days=1)))})
    db.commit()

    run_student_reminders(db, now=_msk(day, 8))
    assert _cycle_notes(db, student) == []  # не ночью и не утром

    run_student_reminders(db, now=_msk(day, 10, 5))
    run_student_reminders(db, now=_msk(day, 10, 35))
    notes = _cycle_notes(db, student)
    assert len(notes) == 1
    assert "не закрыт" in notes[0].title
    assert "запишется как сданное позже" in notes[0].text
    # Ключ общий с кнопкой «Напомнить всем»: сегодня ему уже напомнили.
    assert cycle_debtors(db, cycle, now=_msk(day, 11))[0]["reminded_today"] is True


def test_debt_older_than_reminders_is_not_reminded(db, regular_user, user_factory):
    """Владелец 06.10.2026: «предобучение нужно исключить» — долг со сроком
    до включения рассылки автоматически не напоминается."""
    student = user_factory(vk_id=100_304, name="Старый долг", tariff="Я САМ")
    cycle = _topic(
        db, regular_user, title="Предобучение 1",
        starts_on=date(2026, 9, 20), ends_on=date(2026, 10, 4),
    )
    _undated_task(db, regular_user, cycle, title="Задание предобучения")

    run_student_reminders(db, now=_msk(max(TODAY, date(2026, 10, 7)), 10, 5))
    assert _cycle_notes(db, student) == []
    # Кнопка «Напомнить всем» его по-прежнему видит.
    assert student.id in {row["user"].id for row in cycle_debtors(db, cycle)}


def test_no_cycle_reminder_once_cycle_is_closed(db, regular_user, user_factory):
    student = user_factory(vk_id=100_303, name="Сдал", tariff="Я САМ")
    cycle = _running_cycle(db, regular_user, title="Цикл 1")
    set_topic_tariff_windows(db, cycle, {"Я САМ": (None, _day_end(TODAY - timedelta(days=1)))})
    task = db.query(TrackerTask).filter_by(topic_id=cycle.id).one()
    db.add(TrackerTaskState(task_id=task.id, user_id=student.id, status=STATUS_DONE))
    db.commit()

    run_student_reminders(db, now=_msk(TODAY, 10, 5))
    assert _cycle_notes(db, student) == []


# ── архив по этапу и будущие циклы ──────────────────────────────────────────

def _stage_with_cycles(db, owner, *, stage_ends_on):
    stage = _topic(
        db, owner, title="Октябрь", kind=TOPIC_KIND_STAGE,
        starts_on=TODAY - timedelta(days=20), ends_on=stage_ends_on,
    )
    past = _topic(
        db, owner, title="Цикл 1", parent=stage,
        starts_on=TODAY - timedelta(days=20), ends_on=TODAY - timedelta(days=10),
    )
    current = _topic(
        db, owner, title="Цикл 2", parent=stage,
        starts_on=TODAY - timedelta(days=9), ends_on=TODAY + timedelta(days=3),
    )
    future = _topic(
        db, owner, title="Цикл 3", parent=stage,
        starts_on=TODAY + timedelta(days=4), ends_on=TODAY + timedelta(days=10),
    )
    for cycle in (past, current, future):
        _undated_task(db, owner, cycle, title=f"Задание: {cycle.title}")
    return stage, past, current, future


def _close(db, student, *cycles):
    for cycle in cycles:
        for task in db.query(TrackerTask).filter_by(topic_id=cycle.id):
            db.add(TrackerTaskState(task_id=task.id, user_id=student.id, status=STATUS_DONE))
    db.commit()


def test_past_cycle_of_running_stage_is_not_archive(db, regular_user):
    """«В архив должно уходить не весь цикл, а весь этап»."""
    _student(db, regular_user, "Я САМ")
    _, past, current, future = _stage_with_cycles(
        db, regular_user, stage_ends_on=TODAY + timedelta(days=10),
    )
    # Выполненный цикл идущего этапа — только в карусели, не в архиве.
    _close(db, regular_user, past)

    assert not cycle_is_archived_for_user(db, regular_user.id, past.id, TODAY)
    assert archive_cycle_ids(db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY) == set()

    feed = feed_for_student(db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY)
    chips = {c["id"]: c for c in feed["cycles"]}
    assert feed["topic"].id == current.id
    assert past.id in chips and not chips[past.id]["is_locked"]
    # Будущий цикл этапа виден закрытым, с датой открытия.
    assert chips[future.id]["is_locked"] is True
    assert chips[future.id]["opens_on"] == TODAY + timedelta(days=4)
    assert [c["id"] for c in feed["cycles"]][0] == future.id

    feed_past = feed_for_student(
        db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY, cycle_id=past.id,
    )
    assert feed_past["is_archive"] is False


def test_cycles_go_to_archive_with_their_stage(db, regular_user):
    _student(db, regular_user, "Я САМ")
    stage, past, current, _ = _stage_with_cycles(
        db, regular_user, stage_ends_on=TODAY - timedelta(days=1),
    )
    # Закрыть «Цикл 1», иначе он остался бы текущим долгом.
    _close(db, regular_user, past, current)

    archived = archive_cycle_ids(db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY)
    assert past.id in archived
    assert cycle_is_archived_for_user(db, regular_user.id, past.id, TODAY)


def test_debt_cycle_of_ended_stage_stays_current(db, regular_user):
    """Долг держит и после конца этапа: цикл рабочий, в архив не уходит."""
    _student(db, regular_user, "Я САМ")
    _, past, _, _ = _stage_with_cycles(
        db, regular_user, stage_ends_on=TODAY - timedelta(days=1),
    )

    assert effective_cycle(db, regular_user.id, TODAY).id == past.id
    assert not cycle_is_archived_for_user(db, regular_user.id, past.id, TODAY)
    assert past.id not in archive_cycle_ids(
        db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY,
    )


# ── форма цикла ─────────────────────────────────────────────────────────────

def _cycle_payload(**over):
    data = {
        "title": "Цикл с тарифами",
        "starts_on": TODAY.isoformat(),
        "ends_on": (TODAY + timedelta(days=14)).isoformat(),
        "is_published": True,
    }
    data.update(over)
    return data


def _rows(db, topic_id):
    return {
        row.tariff: row
        for row in db.query(LearningTopicTariff).filter(LearningTopicTariff.topic_id == topic_id)
    }


def test_form_saves_tariff_windows_as_whole_days(admin_client, db):
    client, _ = admin_client
    resp = client.post(CYCLES_PAGE, json=_cycle_payload(tariff_access=[
        {"tariff": "Я САМ", "starts_on": (TODAY + timedelta(days=1)).isoformat(),
         "ends_on": (TODAY + timedelta(days=7)).isoformat()},
        {"tariff": "я с вами", "starts_on": None, "ends_on": None},
    ]))
    assert resp.status_code == 200, resp.text
    cycle = db.get(LearningTopic, resp.json()["cycle_id"])

    assert cycle.tariff_restricted is True
    rows = _rows(db, cycle.id)
    assert set(rows) == {"Я САМ", "Я С ВАМИ"}
    assert _utc(rows["Я САМ"].opens_at) == _day_start(TODAY + timedelta(days=1))
    assert _utc(rows["Я САМ"].closes_at) == _day_end(TODAY + timedelta(days=7))
    assert rows["Я С ВАМИ"].opens_at is None and rows["Я С ВАМИ"].closes_at is None

    page = client.get(CYCLES_PAGE).text
    assert "Доступ по тарифам" in page
    assert f"Я САМ с {(TODAY + timedelta(days=1)).isoformat()}" in page


def test_form_without_the_field_keeps_tariff_access(admin_client, db):
    """Вкладка, открытая до выкатки, поле не присылает — доступ не сносится."""
    client, _ = admin_client
    resp = client.post(CYCLES_PAGE, json=_cycle_payload(tariff_access=[
        {"tariff": "Я САМ", "starts_on": None, "ends_on": None},
    ]))
    cycle_id = resp.json()["cycle_id"]

    assert client.post(f"{CYCLES_PAGE}/{cycle_id}", json=_cycle_payload()).status_code == 200
    db.expire_all()
    assert db.get(LearningTopic, cycle_id).tariff_restricted is True
    assert set(_rows(db, cycle_id)) == {"Я САМ"}

    # Явный null — снова открыт всем.
    resp = client.post(f"{CYCLES_PAGE}/{cycle_id}", json=_cycle_payload(tariff_access=None))
    assert resp.status_code == 200
    db.expire_all()
    assert db.get(LearningTopic, cycle_id).tariff_restricted is False
    assert _rows(db, cycle_id) == {}


def test_form_rejects_end_before_start_unknown_tariff_and_duplicates(admin_client, db):
    client, _ = admin_client
    bad = [
        [{"tariff": "Я САМ", "starts_on": (TODAY + timedelta(days=5)).isoformat(),
          "ends_on": TODAY.isoformat()}],
        [{"tariff": "ПЛАТИНА"}],
        [{"tariff": "Я САМ"}, {"tariff": "я сам"}],
    ]
    for access in bad:
        resp = client.post(CYCLES_PAGE, json=_cycle_payload(tariff_access=access))
        assert resp.status_code == 422, (access, resp.text)
    assert db.query(LearningTopic).filter(LearningTopic.kind == TOPIC_KIND_WEEK).count() == 0


def test_student_side_sees_cycle_only_in_window(db, regular_user):
    """Связка с лентой: до «с» у тарифа цикла нет и в ленте."""
    _student(db, regular_user, "Я САМ")
    cycle = _running_cycle(db, regular_user)
    set_topic_tariff_windows(
        db, cycle, {"Я САМ": (_day_start(TODAY + timedelta(days=1)), None)}
    )
    db.commit()

    feed = feed_for_student(db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY)
    assert feed["topic"] is None or feed["topic"].id != cycle.id
    assert cycle.id not in {c["id"] for c in feed["cycles"]}
