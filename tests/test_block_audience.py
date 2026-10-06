"""Кому доступен блок: тарифы, уровень точки А, ученики поимённо; срок цикла
у ученика.

Владелец 06.10.2026: «добавление точечного доступа по юзернейму… кроме
доступа по тарифу будет ещё по юзернейму», «задание по уровню». Правило:
ученики поимённо складываются с остальным, тариф и уровень вместе сужают.
Там же: «срок по… показывать ученику, что по какой у него срок идёт».
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.task_block import (
    BLOCK_PHOTO_UPLOAD, BLOCK_TEXT, TaskBlockLevel, TaskBlockStudent,
)
from app.models.tracker import TrackerTask
from app.services.cycle_feed import deadline_view, feed_for_student
from app.services.task_blocks import (
    BlockAudience,
    BlockViewer,
    block_student_choices,
    feed_state,
    get_audiences,
    get_blocks,
    get_levels,
    get_student_ids,
    is_block_open_to,
    sync_blocks,
    unfinished_required_steps,
    visible_blocks_for_student,
)
from app.services.tz import MSK_TZ
from app.services.video_topics import set_topic_tariff_windows

from tests.test_cycle_tariff_access import TODAY, _running_cycle, _student
from tests.test_program_take_blocks import (
    PROGRAM, _create_item, _login_chief, _stage,
)


def _viewer(user_id=1, tariff="Я САМ", level=None):
    return BlockViewer(None, user_id=user_id, tariff=tariff, level=level)


def _task(db):
    task = TrackerTask(title="Задание", kind="material")
    db.add(task)
    db.flush()
    return task


def _text(body, **extra):
    return {"block_type": BLOCK_TEXT, "body": body, **extra}


# ── правило ─────────────────────────────────────────────────────────────────

def test_nothing_checked_is_open_to_everyone():
    assert is_block_open_to(None, _viewer())
    assert is_block_open_to(BlockAudience(), _viewer())


def test_named_student_sees_block_whatever_tariff_and_level():
    audience = BlockAudience(
        tariffs=frozenset({"Я С ВАМИ"}), levels=frozenset({2}), user_ids=frozenset({7}),
    )
    assert is_block_open_to(audience, _viewer(user_id=7, tariff="Я САМ", level=1))
    assert not is_block_open_to(audience, _viewer(user_id=8, tariff="Я САМ", level=1))


def test_only_named_students_see_a_students_only_block():
    audience = BlockAudience(user_ids=frozenset({7}))
    assert is_block_open_to(audience, _viewer(user_id=7))
    assert not is_block_open_to(audience, _viewer(user_id=8))


def test_tariff_and_level_together_narrow():
    audience = BlockAudience(tariffs=frozenset({"Я САМ"}), levels=frozenset({2}))
    assert is_block_open_to(audience, _viewer(tariff="Я САМ", level=2))
    assert not is_block_open_to(audience, _viewer(tariff="Я САМ", level=1))
    assert not is_block_open_to(audience, _viewer(tariff="Я С ВАМИ", level=2))


def test_level_alone_ignores_tariff_and_needs_a_level():
    audience = BlockAudience(levels=frozenset({1}))
    assert is_block_open_to(audience, _viewer(tariff="Я С ВАМИ", level=1))
    assert not is_block_open_to(audience, _viewer(level=2))
    assert not is_block_open_to(audience, _viewer(level=None))


def test_viewer_level_is_counted_once_and_only_when_asked(db, regular_user, monkeypatch):
    calls = []

    def fake_level(_db, student):
        calls.append(student.id)
        return 2

    monkeypatch.setattr("app.services.point_a.student_point_a_level", fake_level)
    viewer = BlockViewer(db, user_id=regular_user.id, tariff="Я САМ")
    assert is_block_open_to(BlockAudience(tariffs=frozenset({"Я САМ"})), viewer)
    assert calls == []
    assert viewer.level == 2 and viewer.level == 2
    assert calls == [regular_user.id]


# ── хранение ────────────────────────────────────────────────────────────────

def test_sync_keeps_students_and_levels_and_drops_junk(db, regular_user, user_factory):
    curator = user_factory(vk_id=700_901, role_name="куратор")
    task = _task(db)
    [block] = sync_blocks(db, task_id=task.id, items=[_text(
        "Только для своих",
        levels=[2, 2, 5, "x"],
        student_ids=[regular_user.id, regular_user.id, curator.id, 999_999],
    )])
    db.commit()

    assert get_levels(db, [block.id]) == {block.id: {2}}
    assert get_student_ids(db, [block.id]) == {block.id: [regular_user.id]}
    assert get_audiences(db, [block.id])[block.id] == BlockAudience(
        levels=frozenset({2}), user_ids=frozenset({regular_user.id}),
    )


def test_removed_block_takes_its_students_and_levels(db, regular_user):
    task = _task(db)
    sync_blocks(db, task_id=task.id, items=[
        _text("Уйдёт", levels=[1], student_ids=[regular_user.id]),
    ])
    db.commit()
    sync_blocks(db, task_id=task.id, items=[_text("Останется")])
    db.commit()

    assert db.query(TaskBlockStudent).count() == 0
    assert db.query(TaskBlockLevel).count() == 0


def test_student_choices_are_active_students_with_nick(db, regular_user, user_factory):
    regular_user.first_name, regular_user.last_name = "Маша", "Иванова"
    regular_user.tg_username = "@masha"
    user_factory(vk_id=700_902, role_name="куратор")
    db.commit()

    assert block_student_choices(db) == [{
        "id": regular_user.id, "name": "Иванова Маша",
        "username": "masha", "tariff": regular_user.tariff,
    }]


# ── лента ───────────────────────────────────────────────────────────────────

def test_feed_shows_named_block_only_to_its_student(db, regular_user, user_factory):
    other = user_factory(vk_id=700_903)
    task = _task(db)
    sync_blocks(db, task_id=task.id, items=[
        _text("Всем"), _text("Только Маше", student_ids=[regular_user.id]),
    ])
    db.commit()

    def bodies(user):
        return [
            block.body for block in visible_blocks_for_student(
                db, get_blocks(db, task.id),
                viewer=BlockViewer(db, user_id=user.id, tariff=user.tariff),
            )
        ]

    assert bodies(regular_user) == ["Всем", "Только Маше"]
    assert bodies(other) == ["Всем"]


def test_required_block_of_another_student_does_not_lock_the_feed(db, regular_user, user_factory):
    """Обязательный блок «только Маше» другому ученику не виден — и очередь
    ему не запирает: иначе тупик, сделать невидимое нельзя."""
    other = user_factory(vk_id=700_904)
    task = _task(db)
    sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_PHOTO_UPLOAD, "title": "Маше", "body": "Сдай работу",
         "is_required": True, "student_ids": [regular_user.id]},
        {"block_type": BLOCK_PHOTO_UPLOAD, "title": "Всем", "body": "Сдай вторую",
         "is_required": True},
    ])
    db.commit()

    other_feed = feed_state(db, task_id=task.id, user_id=other.id, user_tariff=other.tariff)
    assert [entry["block"].title for entry in other_feed] == ["Всем"]
    assert other_feed[0]["status"] != "locked"
    assert [b.title for b in unfinished_required_steps(
        db, task_id=task.id, user_id=other.id, user_tariff=other.tariff,
    )] == ["Всем"]

    own_feed = feed_state(
        db, task_id=task.id, user_id=regular_user.id, user_tariff=regular_user.tariff,
    )
    assert [entry["block"].title for entry in own_feed] == ["Маше", "Всем"]


# ── форма ───────────────────────────────────────────────────────────────────

def test_form_saves_and_copies_students_and_levels(client, db, user_factory, session_factory):
    _login_chief(client, user_factory, session_factory)
    student = user_factory(vk_id=700_905, name="Маша")
    stage = _stage(client, db, "Семестр")
    task_id = _create_item(client, stage.id, "Разбор", [
        {"block_type": BLOCK_TEXT, "body": "Личное", "tariffs": [],
         "levels": [2], "student_ids": [student.id]},
    ])

    page = client.get(f"{PROGRAM}/cycles/{stage.id}")
    assert page.status_code == 200
    assert '"student_ids": [%d]' % student.id in page.text
    assert '"levels": [2]' in page.text
    assert "var BLOCK_STUDENTS = [" in page.text and '"id": %d' % student.id in page.text

    source = client.get(f"{PROGRAM}/blocks-source/{task_id}")
    assert source.status_code == 200, source.text
    [copied] = source.json()["blocks"]
    assert copied["student_ids"] == [student.id] and copied["levels"] == [2]


# ── срок цикла у ученика ────────────────────────────────────────────────────

def test_deadline_view_shows_midnight_as_end_of_previous_day():
    midnight = datetime(2026, 10, 13, 0, 0, tzinfo=MSK_TZ)
    assert deadline_view(midnight) == {"text": "12.10 в 23:59", "short": "12.10"}
    evening = datetime(2026, 10, 12, 20, 0, tzinfo=MSK_TZ).astimezone(timezone.utc)
    assert deadline_view(evening) == {"text": "12.10 в 20:00", "short": "12.10"}


@pytest.mark.parametrize("own_end", [False, True])
def test_student_feed_carries_cycle_deadline(db, regular_user, own_end):
    _student(db, regular_user, "Я САМ")
    cycle = _running_cycle(db, regular_user)
    end_day = TODAY + timedelta(days=10)
    expected = f"{end_day:%d.%m} в 23:59"
    if own_end:
        due = TODAY + timedelta(days=4)
        closes = datetime(due.year, due.month, due.day, 20, 0, tzinfo=MSK_TZ)
        set_topic_tariff_windows(db, cycle, {"Я САМ": (None, closes.astimezone(timezone.utc))})
        db.commit()
        expected = f"{due:%d.%m} в 20:00"

    feed = feed_for_student(db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY)

    assert feed["topic"].id == cycle.id
    assert feed["deadline"]["text"] == expected
    assert [c["deadline"]["text"] for c in feed["cycles"] if c["id"] == cycle.id] == [expected]
