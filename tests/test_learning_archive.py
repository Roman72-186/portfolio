"""Архив ученика: видео прошедших этапов, циклов и заданий (владелец 04.10.2026).

Прецедент: ученица не нашла видео прошлых циклов («на платформе их уже нет»).
Полоса циклов на экране обучения показывает только текущий этап, и со сменой
этапа прошлые ролики пропали из виду. Архив собирает ролики из той же ленты,
что видит ученик, — своих правил видимости у него нет.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import TARIFF_SELF
from app.models.learning_topic import TOPIC_KIND_STAGE, TOPIC_KIND_WEEK, LearningTopic
from app.models.learning_video import LearningVideo
from app.models.task_block import BLOCK_VIDEO
from app.models.tracker import ITEM_VIDEO
from app.services.cycle_feed import archive_for_student
from app.services.task_blocks import sync_blocks
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()


@pytest.fixture(autouse=True)
def _bunny_enabled(monkeypatch):
    monkeypatch.setattr("app.services.video_catalog.settings.bunny_stream_enabled", True)


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _topic(db, owner, *, title, starts_on, ends_on, kind=TOPIC_KIND_WEEK, parent=None):
    topic = LearningTopic(
        title=title, opens_at=_utc(msk_midnight(starts_on)),
        ends_at=_utc(msk_midnight(ends_on) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=kind, created_by_id=owner.id,
        parent_id=parent.id if parent is not None else None,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _video(db, title, *, status="ready", is_published=True, topic_id=None):
    video = LearningVideo(
        bunny_library_id=1, bunny_video_id=f"bunny-{title}", title=title,
        status=status, is_published=is_published, topic_id=topic_id,
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def _task(db, owner, topic, *, title, due_at=None, required=False, kind="material"):
    task = create_task(
        db, title=title, user_id=owner.id, kind=kind, topic_id=topic.id,
        assign_to_all=True, is_required=required, due_at=due_at,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _video_task(db, owner, topic, video, *, title=None, due_at=None, tariffs=None):
    task = _task(db, owner, topic, title=title or f"Задание {video.title}", due_at=due_at)
    sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_VIDEO, "video_id": video.id, "tariffs": tariffs or []},
    ])
    db.commit()
    return task


def _program(db, owner):
    """«Предобучение» прошло, идёт «Семестр 1» — ровно случай из жалобы."""
    pre = _topic(db, owner, title="Предобучение", kind=TOPIC_KIND_STAGE,
                 starts_on=TODAY - timedelta(days=60), ends_on=TODAY - timedelta(days=10))
    pre1 = _topic(db, owner, title="Цикл 1", parent=pre,
                  starts_on=TODAY - timedelta(days=60), ends_on=TODAY - timedelta(days=40))
    sem = _topic(db, owner, title="Семестр 1", kind=TOPIC_KIND_STAGE,
                 starts_on=TODAY - timedelta(days=9), ends_on=TODAY + timedelta(days=90))
    sem1 = _topic(db, owner, title="Цикл 1", parent=sem,
                  starts_on=TODAY - timedelta(days=9), ends_on=TODAY + timedelta(days=10))
    return pre, pre1, sem, sem1


def _titles(periods):
    titles = []
    for period in periods:
        titles += [item["video"].title for item in period["stage_videos"]]
        for month in period["months"]:
            for cycle in month["cycles"]:
                titles += [item["video"].title for item in cycle["videos"]]
    return titles


def _archive(db, user):
    return archive_for_student(db, user_id=user.id, user_tariff=user.tariff, today=TODAY)


def test_video_of_finished_stage_is_in_archive(db, regular_user):
    pre, pre1, _, sem1 = _program(db, regular_user)
    _video_task(db, regular_user, pre1, _video(db, "Формообразование"))
    _video_task(db, regular_user, sem1, _video(db, "Идёт сейчас"))

    periods = _archive(db, regular_user)

    assert [p["stage"].id for p in periods] == [pre.id]
    assert _titles(periods) == ["Формообразование"]
    assert periods[0]["months"][0]["cycles"][0]["id"] == pre1.id


def test_video_of_task_with_passed_due_in_current_cycle_is_in_archive(db, regular_user):
    _, _, sem, sem1 = _program(db, regular_user)
    now = datetime.now(timezone.utc)
    _video_task(db, regular_user, sem1, _video(db, "Срок прошёл"),
                due_at=now - timedelta(days=1))
    _video_task(db, regular_user, sem1, _video(db, "Срок впереди"),
                due_at=now + timedelta(days=1))

    periods = _archive(db, regular_user)

    assert _titles(periods) == ["Срок прошёл"]
    assert periods[0]["stage"].id == sem.id


def test_draft_and_unready_videos_are_skipped(db, regular_user):
    _, pre1, _, _ = _program(db, regular_user)
    _video_task(db, regular_user, pre1, _video(db, "Черновик", is_published=False))
    _video_task(db, regular_user, pre1, _video(db, "Кодируется", status="processing"))

    assert _archive(db, regular_user) == []


def test_block_closed_by_another_tariff_is_skipped(db, regular_user):
    assert regular_user.tariff != TARIFF_SELF
    _, pre1, _, _ = _program(db, regular_user)
    _video_task(db, regular_user, pre1, _video(db, "Чужой тариф"), tariffs=[TARIFF_SELF])

    assert _archive(db, regular_user) == []


def test_cycle_locked_by_debt_is_skipped(db, regular_user):
    """Вперёд нельзя: должник предобучения не видит роликов следующих циклов."""
    pre, pre1, _, _ = _program(db, regular_user)
    _task(db, regular_user, pre1, title="Долг", required=True)
    pre2 = _topic(db, regular_user, title="Цикл 2", parent=pre,
                  starts_on=TODAY - timedelta(days=30), ends_on=TODAY - timedelta(days=12))
    _video_task(db, regular_user, pre2, _video(db, "За долгом"))

    assert "За долгом" not in _titles(_archive(db, regular_user))


def test_cycle_ended_before_arrival_is_skipped(db, regular_user):
    _, pre1, _, _ = _program(db, regular_user)
    _video_task(db, regular_user, pre1, _video(db, "До прихода"))
    regular_user.program_access_from = _utc(msk_midnight(TODAY - timedelta(days=5)))
    db.commit()

    assert _archive(db, regular_user) == []


def test_stage_own_task_video_goes_to_stage_group(db, regular_user):
    pre, _, _, _ = _program(db, regular_user)
    _video_task(db, regular_user, pre, _video(db, "Портфолио"))

    periods = _archive(db, regular_user)

    assert [item["video"].title for item in periods[0]["stage_videos"]] == ["Портфолио"]


def test_one_video_in_two_tasks_is_listed_once(db, regular_user):
    _, pre1, _, _ = _program(db, regular_user)
    video = _video(db, "Дважды")
    _video_task(db, regular_user, pre1, video, title="Первое")
    _video_task(db, regular_user, pre1, video, title="Второе")

    assert _titles(_archive(db, regular_user)) == ["Дважды"]


def test_legacy_video_task_without_blocks_is_in_archive(db, regular_user):
    """Задание-видео до конструктора: ролик привязан к теме задания."""
    _, pre1, _, _ = _program(db, regular_user)
    _video(db, "Старый урок", topic_id=pre1.id)
    _task(db, regular_user, pre1, title="Посмотреть урок", kind=ITEM_VIDEO)

    assert _titles(_archive(db, regular_user)) == ["Старый урок"]


def test_page_lists_videos_with_player_links(auth_client, db):
    client, user = auth_client
    _, pre1, _, _ = _program(db, user)
    video = _video(db, "Формообразование")
    _video_task(db, user, pre1, video, title="Узлы")

    resp = client.get("/cabinet/learning/archive")

    assert resp.status_code == 200
    assert "Предобучение" in resp.text
    assert f'href="/cabinet/videos/{video.id}"' in resp.text
    assert "Узлы" in resp.text
    assert "Смотреть" in resp.text
    # Ссылок в ленту циклов в архиве больше нет — только ролики.
    assert "/cabinet/learning?cycle=" not in resp.text


def test_empty_archive_explains_when_videos_appear(auth_client):
    client, _ = auth_client

    resp = client.get("/cabinet/learning/archive")

    assert resp.status_code == 200
    assert "Видео появятся здесь" in resp.text
