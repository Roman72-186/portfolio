"""Доступ к ролику, поставленному блоком конструктора.

Ловушка, ради которой этот файл существует: до конструктора ролик без темы
(`topic_id IS NULL`) считался открытым всем ученикам — так вели себя все
ролики до появления тем. Конструктор `topic_id` не пишет, поэтому без
отдельной ветки каждый поставленный ролик утёк бы всей школе через каталог
`/cabinet/videos`.

Контракт: ролик, стоящий блоком хоть где-то, считается только по блокам;
ролик без блоков ведёт себя ровно как раньше.
"""

import pytest

from app.models.learning_topic import LearningTopic
from app.models.learning_video import LearningVideo
from app.models.task_block import BLOCK_VIDEO
from app.models.tracker import TrackerTask
from app.services.task_blocks import sync_blocks
from app.services.tz import now_msk
from app.services.video_catalog import (
    is_video_accessible,
    list_published_videos,
    block_bound_video_ids,
)


@pytest.fixture(autouse=True)
def _bunny_enabled(monkeypatch):
    """`list_published_videos` без включённого модуля возвращает пустой список."""
    monkeypatch.setattr("app.services.video_catalog.settings.bunny_stream_enabled", True)


def _video(db, *, title="Урок", bunny_id="v-1", topic_id=None) -> LearningVideo:
    video = LearningVideo(
        bunny_library_id=1,
        bunny_video_id=bunny_id,
        title=title,
        topic_id=topic_id,
        status="ready",
        is_published=True,
    )
    db.add(video)
    db.flush()
    return video


def _task(db, *, assign_to_all: bool, title="Материал") -> TrackerTask:
    task = TrackerTask(
        title=title,
        kind="material",
        is_published=True,
        assign_to_all=assign_to_all,
    )
    db.add(task)
    db.flush()
    return task


def _put_video_in_task(db, task: TrackerTask, video: LearningVideo) -> None:
    sync_blocks(
        db, task_id=task.id, items=[{"block_type": BLOCK_VIDEO, "video_id": video.id}]
    )


def _viewer(user) -> dict:
    return {"user_id": user.id, "role_rank": 1}


# --- старое поведение не меняется -------------------------------------------


def test_video_without_blocks_and_without_topic_stays_open_to_everyone(db, user_factory):
    """Регресс на легаси: ролики, залитые до появления тем, не должны закрыться."""
    student = user_factory(vk_id=710_001, name="Ученик")
    video = _video(db)
    db.commit()

    assert is_video_accessible(db, video, _viewer(student)) is True
    assert video in list_published_videos(db, viewer=_viewer(student))


def test_video_bound_to_closed_topic_stays_closed(db, user_factory):
    student = user_factory(vk_id=710_002, name="Ученик")
    topic = LearningTopic(
        title="Чужая неделя",
        is_published=True,
        assign_to_all=False,
        opens_at=now_msk(),
    )
    db.add(topic)
    db.flush()
    video = _video(db, topic_id=topic.id)
    db.commit()

    assert is_video_accessible(db, video, _viewer(student)) is False


def test_video_bound_to_open_topic_is_accessible(db, user_factory):
    student = user_factory(vk_id=710_003, name="Ученик")
    topic = LearningTopic(
        title="Общая неделя",
        is_published=True,
        assign_to_all=True,
        opens_at=now_msk(),
    )
    db.add(topic)
    db.flush()
    video = _video(db, topic_id=topic.id)
    db.commit()

    assert is_video_accessible(db, video, _viewer(student)) is True


# --- новое правило: доступ считается по блокам -------------------------------


def test_video_in_block_of_inaccessible_task_is_closed(db, user_factory):
    """Главная ловушка: у ролика нет темы, но открывать его всем нельзя."""
    student = user_factory(vk_id=710_004, name="Ученик")
    video = _video(db)
    task = _task(db, assign_to_all=False, title="Не для всех")
    _put_video_in_task(db, task, video)
    db.commit()

    assert block_bound_video_ids(db) == {video.id}
    assert is_video_accessible(db, video, _viewer(student)) is False
    assert list_published_videos(db, viewer=_viewer(student)) == []


def test_video_in_block_of_accessible_task_is_open(db, user_factory):
    student = user_factory(vk_id=710_005, name="Ученик")
    video = _video(db)
    task = _task(db, assign_to_all=True, title="Всем")
    _put_video_in_task(db, task, video)
    db.commit()

    assert is_video_accessible(db, video, _viewer(student)) is True
    assert video in list_published_videos(db, viewer=_viewer(student))


def test_one_accessible_task_is_enough(db, user_factory):
    """Один ролик разрешено ставить в несколько заданий — хватает одного открытого."""
    student = user_factory(vk_id=710_006, name="Ученик")
    video = _video(db)
    closed = _task(db, assign_to_all=False, title="Закрытое")
    opened = _task(db, assign_to_all=True, title="Открытое")
    _put_video_in_task(db, closed, video)
    _put_video_in_task(db, opened, video)
    db.commit()

    assert is_video_accessible(db, video, _viewer(student)) is True


def test_unpublished_task_does_not_open_its_video(db, user_factory):
    student = user_factory(vk_id=710_007, name="Ученик")
    video = _video(db)
    task = _task(db, assign_to_all=True, title="Черновик")
    task.is_published = False
    _put_video_in_task(db, task, video)
    db.commit()

    assert is_video_accessible(db, video, _viewer(student)) is False


def test_removing_block_returns_video_to_legacy_behaviour(db, user_factory):
    """Убрали ролик из всех заданий — он снова «ничей» и ведёт себя как легаси."""
    student = user_factory(vk_id=710_008, name="Ученик")
    video = _video(db)
    task = _task(db, assign_to_all=False)
    _put_video_in_task(db, task, video)
    db.commit()
    assert is_video_accessible(db, video, _viewer(student)) is False

    sync_blocks(db, task_id=task.id, items=[])
    db.commit()

    assert block_bound_video_ids(db) == set()
    assert is_video_accessible(db, video, _viewer(student)) is True


def test_staff_sees_video_regardless_of_blocks(db, user_factory):
    curator = user_factory(vk_id=710_009, name="Куратор", role_name="куратор")
    video = _video(db)
    task = _task(db, assign_to_all=False)
    _put_video_in_task(db, task, video)
    db.commit()

    viewer = {"user_id": curator.id, "role_rank": 2}
    assert is_video_accessible(db, video, viewer) is True
    assert video in list_published_videos(db, viewer=viewer)


def test_catalog_mixes_block_bound_and_legacy_videos(db, user_factory):
    """Список и прямой заход по ссылке обязаны решать одинаково."""
    student = user_factory(vk_id=710_010, name="Ученик")
    legacy = _video(db, title="Легаси", bunny_id="v-legacy")
    hidden = _video(db, title="Скрытый", bunny_id="v-hidden")
    shown = _video(db, title="Показанный", bunny_id="v-shown")
    _put_video_in_task(db, _task(db, assign_to_all=False), hidden)
    _put_video_in_task(db, _task(db, assign_to_all=True), shown)
    db.commit()

    catalog = list_published_videos(db, viewer=_viewer(student))
    assert {v.id for v in catalog} == {legacy.id, shown.id}
    for video in (legacy, shown):
        assert is_video_accessible(db, video, _viewer(student)) is True
    assert is_video_accessible(db, hidden, _viewer(student)) is False
