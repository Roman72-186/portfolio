"""Архив ученика: прошедшие циклы и видео прошедших этапов, циклов и заданий
(владелец 04.10.2026; цикл целиком — служба заботы 05.10.2026).

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
from app.services.tracker import close_task_for_user, create_task
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


def test_completed_running_cycle_is_in_archive(db, regular_user):
    """Этап не закрыт, цикл ещё идёт, но ученик его выполнил — ролики цикла
    в архиве (владелец 04.10.2026: «Цикл 4» закрыли до его конца)."""
    _, _, sem, sem1 = _program(db, regular_user)
    _video_task(db, regular_user, sem1, _video(db, "Выполнено"))
    required = _task(db, regular_user, sem1, title="Сдать работу", required=True)
    close_task_for_user(db, required, regular_user.id, source="manual")
    db.commit()

    periods = _archive(db, regular_user)

    assert _titles(periods) == ["Выполнено"]
    assert periods[0]["stage"].id == sem.id


def test_running_cycle_with_open_required_task_is_not_in_archive(db, regular_user):
    _, _, _, sem1 = _program(db, regular_user)
    _video_task(db, regular_user, sem1, _video(db, "Не выполнено"))
    _task(db, regular_user, sem1, title="Сдать работу", required=True)

    assert _archive(db, regular_user) == []


def test_running_cycle_without_required_tasks_is_not_in_archive(db, regular_user):
    """Цикл без обязательных заданий «пройден» для долга сразу, но ученик в
    нём ничего не сделал — его ролики не уезжают в архив с первого дня
    (прод 04.10.2026: «Подготовка к годовому курсу»)."""
    _, _, _, sem1 = _program(db, regular_user)
    _video_task(db, regular_user, sem1, _video(db, "Только началось"))

    assert _archive(db, regular_user) == []


def test_draft_and_unready_videos_are_skipped(db, regular_user):
    _, pre1, _, _ = _program(db, regular_user)
    _video_task(db, regular_user, pre1, _video(db, "Черновик", is_published=False))
    _video_task(db, regular_user, pre1, _video(db, "Кодируется", status="processing"))

    assert _titles(_archive(db, regular_user)) == []


def test_block_closed_by_another_tariff_is_skipped(db, regular_user):
    assert regular_user.tariff != TARIFF_SELF
    _, pre1, _, _ = _program(db, regular_user)
    _video_task(db, regular_user, pre1, _video(db, "Чужой тариф"), tariffs=[TARIFF_SELF])

    assert _titles(_archive(db, regular_user)) == []


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
    assert f'href="/cabinet/learning?cycle={pre1.id}"' in resp.text
    assert "Открыть цикл целиком" in resp.text


def test_empty_archive_explains_when_videos_appear(auth_client):
    client, _ = auth_client

    resp = client.get("/cabinet/learning/archive")

    assert resp.status_code == 200
    assert "Цикл попадёт сюда" in resp.text


def _cycles(periods):
    return {
        cycle["id"]: cycle
        for period in periods for month in period["months"] for cycle in month["cycles"]
    }


def test_finished_cycle_without_videos_is_in_archive_with_open_button(db, regular_user):
    """Служба заботы 05.10.2026: в архиве должен быть весь период — задания и
    работы, а не только ролики. Цикл без видео тоже в архиве, кнопкой."""
    _, pre1, _, _ = _program(db, regular_user)
    _task(db, regular_user, pre1, title="Наброски")

    cycles = _cycles(_archive(db, regular_user))

    assert cycles[pre1.id]["can_open"] is True
    assert cycles[pre1.id]["videos"] == []


def test_completed_running_cycle_can_be_opened(db, regular_user):
    _, _, _, sem1 = _program(db, regular_user)
    required = _task(db, regular_user, sem1, title="Сдать работу", required=True)
    close_task_for_user(db, required, regular_user.id, source="manual")
    db.commit()

    assert _cycles(_archive(db, regular_user))[sem1.id]["can_open"] is True


def test_running_cycle_with_passed_due_video_has_no_open_button(db, regular_user):
    """Идущий невыполненный цикл в архиве только роликом с вышедшим сроком:
    он и так в карусели, кнопка «целиком» ему ни к чему."""
    _, _, _, sem1 = _program(db, regular_user)
    _video_task(db, regular_user, sem1, _video(db, "Срок прошёл"),
                due_at=datetime.now(timezone.utc) - timedelta(days=1))
    _task(db, regular_user, sem1, title="Сдать работу", required=True)

    assert _cycles(_archive(db, regular_user))[sem1.id]["can_open"] is False


def test_finished_cycle_without_tasks_is_not_in_archive(db, regular_user):
    _program(db, regular_user)

    assert _archive(db, regular_user) == []


def test_cycle_locked_by_debt_has_no_open_button(db, regular_user):
    """Вперёд нельзя: запертый долгом цикл не попадает в архив и кнопкой."""
    pre, pre1, _, _ = _program(db, regular_user)
    _task(db, regular_user, pre1, title="Долг", required=True)
    pre2 = _topic(db, regular_user, title="Цикл 2", parent=pre,
                  starts_on=TODAY - timedelta(days=30), ends_on=TODAY - timedelta(days=12))
    _task(db, regular_user, pre2, title="За долгом")

    assert pre2.id not in _cycles(_archive(db, regular_user))


def test_open_button_leads_to_read_only_cycle(auth_client, db):
    """Кнопка ведёт в ленту прошлого цикла: задания на месте, изменить нельзя."""
    client, user = auth_client
    _, pre1, _, _ = _program(db, user)
    _task(db, user, pre1, title="Наброски")

    archive = client.get("/cabinet/learning/archive")
    feed = client.get(f"/cabinet/learning?cycle={pre1.id}")

    assert f'href="/cabinet/learning?cycle={pre1.id}"' in archive.text
    assert feed.status_code == 200
    assert "Наброски" in feed.text
    assert "Пройденный цикл" in feed.text
    # Кнопку сервер отклонил бы: цикл пройден (05.10.2026).
    assert "data-toggle-task" not in feed.text


def test_read_only_cycle_keeps_done_mark(auth_client, db):
    client, user = auth_client
    _, pre1, _, _ = _program(db, user)
    done = _task(db, user, pre1, title="Наброски")
    close_task_for_user(db, done, user.id, source="manual")
    db.commit()

    feed = client.get(f"/cabinet/learning?cycle={pre1.id}")

    assert "Задание выполнено" in feed.text
