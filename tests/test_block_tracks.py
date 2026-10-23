"""Кому доступен блок по треку каникулярного набора.

Владелец 23.10.2026: «доступность блока нужно настроить под каникулярные
теги». Трек ученика — его тег, тарифа у каникулярного нет: в цикле на два
трека («ЭКЗпроба 1 трек» и «2 трек») блоки делить было нечем. Ось «Треки»
сужает, как тариф; внутри неё хватает одного из отмеченных треков; ученик,
выбранный поимённо, видит блок при любом треке.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from app.constants import PROGRAM_HOLIDAY
from app.models.learning_topic import TOPIC_KIND_PERIOD, TOPIC_KIND_STAGE
from app.models.tag import UserTag
from app.models.task_block import BLOCK_TEXT, TaskBlockTag
from app.models.tracker import TrackerTask
from app.services import intake_link as intake_link_service
from app.services.tags import get_or_create_tag
from app.services.task_blocks import (
    BlockAudience,
    BlockViewer,
    get_audiences,
    get_blocks,
    get_tag_ids,
    is_block_open_to,
    sync_blocks,
    visible_blocks_for_student,
)
from app.services.video_topics import set_topic_tags

from tests.test_holiday_program import _topic
from tests.test_program_take_blocks import (
    PROGRAM, _create_item, _login_chief, _stage,
)


def _viewer(user_id=1, tags=(), level=None):
    return BlockViewer(None, user_id=user_id, tariff="", level=level, tag_ids=frozenset(tags))


def _track(db, title):
    tag = get_or_create_tag(db, title)
    link = intake_link_service.create_holiday_link(
        db, title=title, tag_id=tag.id,
        access_until=datetime.now(timezone.utc) + timedelta(days=14), created_by_id=None,
    )
    db.commit()
    return link


def _task(db):
    task = TrackerTask(title="Задание", kind="material")
    db.add(task)
    db.flush()
    return task


def _text(body, **extra):
    return {"block_type": BLOCK_TEXT, "body": body, **extra}


# ── правило ─────────────────────────────────────────────────────────────────

def test_track_narrows_and_any_checked_track_is_enough():
    audience = BlockAudience(tag_ids=frozenset({21, 22}))
    assert is_block_open_to(audience, _viewer(tags={22}))
    assert is_block_open_to(audience, _viewer(tags={5, 21}))
    assert not is_block_open_to(audience, _viewer(tags={23}))
    assert not is_block_open_to(audience, _viewer(tags=()))


def test_named_student_sees_track_block_without_the_track():
    audience = BlockAudience(tag_ids=frozenset({22}), user_ids=frozenset({7}))
    assert is_block_open_to(audience, _viewer(user_id=7, tags=()))
    assert not is_block_open_to(audience, _viewer(user_id=8, tags=()))


def test_track_and_level_together_narrow():
    audience = BlockAudience(tag_ids=frozenset({22}), levels=frozenset({2}))
    assert is_block_open_to(audience, _viewer(tags={22}, level=2))
    assert not is_block_open_to(audience, _viewer(tags={22}, level=1))
    assert not is_block_open_to(audience, _viewer(tags={21}, level=2))


def test_viewer_reads_tags_from_the_base(db, regular_user):
    tag = get_or_create_tag(db, "ЭКЗпроба 2 трек")
    db.add(UserTag(user_id=regular_user.id, tag_id=tag.id))
    db.commit()

    viewer = BlockViewer(db, user_id=regular_user.id, tariff="")
    assert viewer.tag_ids == frozenset({tag.id})


# ── хранение ────────────────────────────────────────────────────────────────

def test_sync_keeps_only_holiday_tracks(db):
    track = _track(db, "ЭКЗпроба 2 трек")
    stranger = get_or_create_tag(db, "Просто тег")
    db.commit()
    task = _task(db)
    [block] = sync_blocks(db, task_id=task.id, items=[_text(
        "Второму треку", tag_ids=[track.tag_id, track.tag_id, stranger.id, "x", 999_999],
    )])
    db.commit()

    assert get_tag_ids(db, [block.id]) == {block.id: [track.tag_id]}
    assert get_audiences(db, [block.id])[block.id] == BlockAudience(
        tag_ids=frozenset({track.tag_id}),
    )


def test_removed_block_takes_its_tracks(db):
    track = _track(db, "ЭКЗпроба 2 трек")
    task = _task(db)
    sync_blocks(db, task_id=task.id, items=[_text("Уйдёт", tag_ids=[track.tag_id])])
    db.commit()
    sync_blocks(db, task_id=task.id, items=[_text("Останется")])
    db.commit()

    assert db.query(TaskBlockTag).count() == 0


# ── лента ───────────────────────────────────────────────────────────────────

def test_feed_shows_track_block_only_to_its_track(db, user_factory):
    first = _track(db, "ЭКЗпроба 1 трек")
    second = _track(db, "ЭКЗпроба 2 трек")
    one = user_factory(vk_id=700_951, tariff="")
    two = user_factory(vk_id=700_952, tariff="")
    db.add_all([
        UserTag(user_id=one.id, tag_id=first.tag_id),
        UserTag(user_id=two.id, tag_id=second.tag_id),
    ])
    task = _task(db)
    sync_blocks(db, task_id=task.id, items=[
        _text("Всем"), _text("Второму треку", tag_ids=[second.tag_id]),
    ])
    db.commit()

    def bodies(user):
        return [
            block.body for block in visible_blocks_for_student(
                db, get_blocks(db, task.id),
                viewer=BlockViewer(db, user_id=user.id, tariff=user.tariff),
            )
        ]

    assert bodies(one) == ["Всем"]
    assert bodies(two) == ["Всем", "Второму треку"]


# ── форма ───────────────────────────────────────────────────────────────────

def _block_tracks(html):
    found = re.search(r"var BLOCK_TRACKS = (\[.*?\]);", html)
    assert found, "на странице нет списка треков"
    return json.loads(found.group(1))


def _holiday_cycle(db, owner, tag_ids):
    period = _topic(db, owner, title="Каникулы", kind=TOPIC_KIND_PERIOD, program=PROGRAM_HOLIDAY)
    stage = _topic(db, owner, title="Каникулы этап", kind=TOPIC_KIND_STAGE, parent=period)
    cycle = _topic(db, owner, title="Экзаменационная проба", parent=stage)
    set_topic_tags(db, cycle, tag_ids)
    db.commit()
    return cycle


def test_holiday_cycle_form_offers_its_tracks_saves_and_copies_them(
    client, db, user_factory, session_factory,
):
    chief = _login_chief(client, user_factory, session_factory)
    first = _track(db, "ЭКЗпроба 1 трек")
    second = _track(db, "ЭКЗпроба 2 трек")
    _track(db, "АРХИпроба 1 трек")
    cycle = _holiday_cycle(db, chief, [first.tag_id, second.tag_id])

    task_id = _create_item(client, cycle.id, "Экзаменационная проба", [
        {"block_type": BLOCK_TEXT, "body": "Практика с проверкой", "tag_ids": [second.tag_id]},
    ])

    page = client.get(f"{PROGRAM}/cycles/{cycle.id}")
    assert page.status_code == 200, page.text
    assert _block_tracks(page.text) == [
        {"id": first.tag_id, "name": "ЭКЗпроба 1 трек"},
        {"id": second.tag_id, "name": "ЭКЗпроба 2 трек"},
    ]
    assert "<select data-cycle-preview-track>" in page.text

    [block] = get_blocks(db, task_id)
    assert get_tag_ids(db, [block.id]) == {block.id: [second.tag_id]}

    source = client.get(f"{PROGRAM}/blocks-source/{task_id}")
    assert source.status_code == 200, source.text
    [copied] = source.json()["blocks"]
    assert copied["tag_ids"] == [second.tag_id]


def test_annual_cycle_form_has_no_tracks(client, db, user_factory, session_factory):
    _login_chief(client, user_factory, session_factory)
    _track(db, "ЭКЗпроба 1 трек")
    stage = _stage(client, db, "Семестр")
    _create_item(client, stage.id, "Разбор", [{"block_type": BLOCK_TEXT, "body": "Текст"}])

    page = client.get(f"{PROGRAM}/cycles/{stage.id}")
    assert page.status_code == 200, page.text
    assert _block_tracks(page.text) == []
    assert "<select data-cycle-preview-track>" not in page.text
