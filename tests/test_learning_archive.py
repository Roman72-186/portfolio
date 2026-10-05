"""Архив ученика: пройденные циклы, период → этап → цикл, цикл открывается
тут же, в архиве (владелец 05.10.2026).

Прецеденты: 04.10.2026 ученица не нашла видео прошлых циклов — полоса циклов
на экране обучения показывает только текущий этап; 05.10.2026 служба заботы:
в архиве только видео, а нужна «полная архивация периода со всеми заданиями,
видео, голосовыми, работами ребенка». Архив берёт циклы и шаги из той же
ленты, что видит ученик, — своих правил видимости у него нет.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.learning_topic import (
    TOPIC_KIND_PERIOD,
    TOPIC_KIND_STAGE,
    TOPIC_KIND_WEEK,
    LearningTopic,
)
from app.models.learning_video import LearningVideo
from app.models.task_block import BLOCK_VIDEO
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


def _archive(db, user):
    return archive_for_student(db, user_id=user.id, user_tariff=user.tariff, today=TODAY)


def _cycle_ids(periods):
    return [
        cycle["id"]
        for period in periods for stage in period["stages"] for cycle in stage["cycles"]
    ]


def test_finished_cycle_is_in_archive_under_period_and_stage(db, regular_user):
    """Этап без периода (данные до 06.10.2026) — период повторяет название
    этапа, как было до уровня «Период» (владелец 05.10.2026)."""
    _, pre1, _, sem1 = _program(db, regular_user)
    _task(db, regular_user, pre1, title="Наброски")
    _task(db, regular_user, sem1, title="Идёт сейчас", required=True)

    periods = _archive(db, regular_user)

    assert [p["title"] for p in periods] == ["Предобучение"]
    assert [s["title"] for s in periods[0]["stages"]] == ["Предобучение"]
    assert _cycle_ids(periods) == [pre1.id]


def test_finished_cycle_without_steps_is_not_in_archive(db, regular_user):
    _program(db, regular_user)

    assert _archive(db, regular_user) == []


def test_completed_running_cycle_is_in_archive(db, regular_user):
    """Этап не закрыт, цикл ещё идёт, но ученик его выполнил — цикл в архиве
    (владелец 04.10.2026: «Цикл 4» закрыли до его конца)."""
    _, _, sem, sem1 = _program(db, regular_user)
    required = _task(db, regular_user, sem1, title="Сдать работу", required=True)
    close_task_for_user(db, required, regular_user.id, source="manual")
    db.commit()

    periods = _archive(db, regular_user)

    assert _cycle_ids(periods) == [sem1.id]
    assert periods[0]["topic"].id == sem.id


def test_running_cycle_with_open_required_task_is_not_in_archive(db, regular_user):
    """Вышедший срок задания идущий цикл в архив не уводит: в архиве только
    пройденные циклы (владелец 05.10.2026: «только циклы»)."""
    _, _, _, sem1 = _program(db, regular_user)
    _video_task(db, regular_user, sem1, _video(db, "Срок прошёл"),
                due_at=datetime.now(timezone.utc) - timedelta(days=1))
    _task(db, regular_user, sem1, title="Сдать работу", required=True)

    assert _archive(db, regular_user) == []


def test_running_cycle_without_required_tasks_is_not_in_archive(db, regular_user):
    """Цикл без обязательных заданий «пройден» для долга сразу, но ученик в
    нём ничего не сделал — в архив он с первого дня не уезжает (прод
    04.10.2026: «Подготовка к годовому курсу»)."""
    _, _, _, sem1 = _program(db, regular_user)
    _task(db, regular_user, sem1, title="Только началось")

    assert _archive(db, regular_user) == []


def test_cycle_locked_by_debt_is_skipped(db, regular_user):
    """Вперёд нельзя: должник предобучения не видит следующих циклов."""
    pre, pre1, _, _ = _program(db, regular_user)
    _task(db, regular_user, pre1, title="Долг", required=True)
    pre2 = _topic(db, regular_user, title="Цикл 2", parent=pre,
                  starts_on=TODAY - timedelta(days=30), ends_on=TODAY - timedelta(days=12))
    _task(db, regular_user, pre2, title="За долгом")

    assert pre2.id not in _cycle_ids(_archive(db, regular_user))


def test_cycle_ended_before_arrival_is_skipped(db, regular_user):
    _, pre1, _, _ = _program(db, regular_user)
    _task(db, regular_user, pre1, title="До прихода")
    regular_user.program_access_from = _utc(msk_midnight(TODAY - timedelta(days=5)))
    db.commit()

    assert _archive(db, regular_user) == []


def test_cycle_without_stage_goes_to_early_cycles(db, regular_user):
    old = _topic(db, regular_user, title="Неделя 1",
                 starts_on=TODAY - timedelta(days=90), ends_on=TODAY - timedelta(days=80))
    _task(db, regular_user, old, title="Старое")

    periods = _archive(db, regular_user)

    assert periods[0]["title"] == "Ранние циклы"
    assert _cycle_ids(periods) == [old.id]


def test_archive_page_lists_cycles_opening_inside_archive(auth_client, db):
    client, user = auth_client
    _, pre1, _, _ = _program(db, user)
    _video_task(db, user, pre1, _video(db, "Формообразование"), title="Узлы")

    resp = client.get("/cabinet/learning/archive")

    assert resp.status_code == 200
    assert "Предобучение" in resp.text
    assert f'href="/cabinet/learning/archive/{pre1.id}"' in resp.text
    # В АОП архив не перекидывает, а ролики смотрят внутри цикла.
    assert "/cabinet/learning?cycle=" not in resp.text
    assert "/cabinet/videos/" not in resp.text


def test_archive_cycle_page_shows_whole_cycle_inside_archive(auth_client, db):
    client, user = auth_client
    _, pre1, _, _ = _program(db, user)
    _task(db, user, pre1, title="Наброски")

    resp = client.get(f"/cabinet/learning/archive/{pre1.id}")

    assert resp.status_code == 200
    assert "Наброски" in resp.text
    assert "Весь архив" in resp.text
    assert 'class="lrn-cycles"' not in resp.text
    # Внизу активна вкладка «Архив», а не АОП.
    assert '<span class="burger-toggle-label">Архив</span>' in resp.text
    # Кнопку сервер отклонил бы: цикл пройден.
    assert "data-toggle-task" not in resp.text


def test_archive_cycle_page_keeps_done_mark(auth_client, db):
    client, user = auth_client
    _, pre1, _, _ = _program(db, user)
    done = _task(db, user, pre1, title="Наброски")
    close_task_for_user(db, done, user.id, source="manual")
    db.commit()

    resp = client.get(f"/cabinet/learning/archive/{pre1.id}")

    assert "Задание выполнено" in resp.text


def test_archive_cycle_page_refuses_cycle_not_in_archive(auth_client, db):
    """Номер текущего цикла в адресной строке не открывает его под видом
    архива — обратно в список."""
    client, user = auth_client
    _, _, _, sem1 = _program(db, user)
    _task(db, user, sem1, title="Идёт сейчас", required=True)

    resp = client.get(f"/cabinet/learning/archive/{sem1.id}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/learning/archive"


def test_empty_archive_explains_when_cycles_appear(auth_client):
    client, _ = auth_client

    resp = client.get("/cabinet/learning/archive")

    assert resp.status_code == 200
    assert "Цикл попадёт сюда" in resp.text


# ── настоящий период над этапом (владелец 06.10.2026: «да, показывать») ─────

def _period_program(db, owner):
    """Как на проде: период «1 семестр» над двумя этапами в одни даты."""
    period = _topic(db, owner, title="1 семестр 2026-2027", kind=TOPIC_KIND_PERIOD,
                    starts_on=TODAY - timedelta(days=60), ends_on=TODAY + timedelta(days=60))
    year = _topic(db, owner, title="1 семестр_годовой курс", kind=TOPIC_KIND_STAGE,
                  parent=period, starts_on=TODAY - timedelta(days=60),
                  ends_on=TODAY + timedelta(days=60))
    october = _topic(db, owner, title="Октябрь", kind=TOPIC_KIND_STAGE, parent=period,
                     starts_on=TODAY - timedelta(days=60), ends_on=TODAY + timedelta(days=60))
    return period, year, october


def test_archive_shows_real_period_above_stage(db, regular_user):
    period, year, _ = _period_program(db, regular_user)
    cycle = _topic(db, regular_user, title="Цикл 1", parent=year,
                   starts_on=TODAY - timedelta(days=30), ends_on=TODAY - timedelta(days=20))
    _task(db, regular_user, cycle, title="Наброски")

    periods = _archive(db, regular_user)

    assert [p["title"] for p in periods] == ["1 семестр 2026-2027"]
    assert periods[0]["topic"].id == period.id
    assert [s["title"] for s in periods[0]["stages"]] == ["1 семестр_годовой курс"]
    assert _cycle_ids(periods) == [cycle.id]


def test_interleaved_stages_of_one_period_are_grouped_once(db, regular_user):
    """Этапы одного периода идут в одни даты, циклы чередуются — каждый этап
    в архиве один раз."""
    _, year, october = _period_program(db, regular_user)
    cycles = []
    for offset, stage in ((50, year), (40, october), (30, year)):
        cycle = _topic(db, regular_user, title=f"Цикл {offset}", parent=stage,
                       starts_on=TODAY - timedelta(days=offset),
                       ends_on=TODAY - timedelta(days=offset - 5))
        _task(db, regular_user, cycle, title=f"Задание {offset}")
        cycles.append(cycle)

    periods = _archive(db, regular_user)

    assert len(periods) == 1
    stages = {s["title"]: [c["id"] for c in s["cycles"]] for s in periods[0]["stages"]}
    assert len(periods[0]["stages"]) == 2
    assert stages["1 семестр_годовой курс"] == [cycles[0].id, cycles[2].id]
    assert stages["Октябрь"] == [cycles[1].id]


def test_hidden_period_is_not_shown_to_student(db, regular_user):
    """Период со снятой галочкой «Показывать ученикам» ученик не видит и в
    архиве — этап стоит сам за себя, как до уровня «Период»."""
    period, year, _ = _period_program(db, regular_user)
    period.is_published = False
    db.commit()
    cycle = _topic(db, regular_user, title="Цикл 1", parent=year,
                   starts_on=TODAY - timedelta(days=30), ends_on=TODAY - timedelta(days=20))
    _task(db, regular_user, cycle, title="Наброски")

    periods = _archive(db, regular_user)

    assert [p["title"] for p in periods] == ["1 семестр_годовой курс"]
    assert periods[0]["topic"].id == year.id


def test_archive_page_does_not_repeat_stage_named_like_period(auth_client, db):
    """Прод: период и этап оба «Предобучение 2026-2027» — подпись этапа не
    повторяется; этап со своим названием («Октябрь») подписан."""
    client, user = auth_client
    pre_period = _topic(db, user, title="Предобучение 2026-2027", kind=TOPIC_KIND_PERIOD,
                        starts_on=TODAY - timedelta(days=60), ends_on=TODAY - timedelta(days=15))
    pre_stage = _topic(db, user, title="Предобучение 2026-2027", kind=TOPIC_KIND_STAGE,
                       parent=pre_period, starts_on=TODAY - timedelta(days=60),
                       ends_on=TODAY - timedelta(days=15))
    _, _, october = _period_program(db, user)
    for stage, offset in ((pre_stage, 50), (october, 20)):
        cycle = _topic(db, user, title="Цикл 1", parent=stage,
                       starts_on=TODAY - timedelta(days=offset),
                       ends_on=TODAY - timedelta(days=offset - 5))
        _task(db, user, cycle, title="Наброски")

    page = client.get("/cabinet/learning/archive").text

    assert page.count("Предобучение 2026-2027") == 1
    assert '<h2 class="lrn-card-title">1 семестр 2026-2027</h2>' in page
    assert '<h3 class="lrn-archive-stage">Октябрь</h3>' in page
