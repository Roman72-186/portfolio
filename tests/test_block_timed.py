"""Контрольная работа на время — блок «Работа на время» (владелец 03.09.2026).

«Здесь в контрольной у нас таймер… давай сделаем один час… мне кажется, здесь
придётся делать так, что ребёнок будет рисовать, как у него получилось, и
будет скидывать. И что мы будем отслеживать статистику, сколько детей
превысили время… их можно будет пометить красненьким.»

Превышение лимита не мешает сдать работу — оно только видно.
"""
from datetime import datetime, timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import (
    BLOCK_TIMED,
    TIMED_DEFAULT_MINUTES,
    TaskBlock,
    TaskBlockState,
)
from app.models.work import Work
from app.services.cycle_feed import build_cycle_feed
from app.services.program import day_bounds
from app.services.task_blocks import (
    get_state,
    start_timed_block,
    sync_blocks,
    timed_overrun,
)
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=1)
CYCLE_END = TODAY + timedelta(days=6)


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    topic = LearningTopic(
        title="Цикл с контрольной", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    return topic


def _task(db, owner):
    task = create_task(
        db, title="Контрольная", user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _timed(db, task, minutes=TIMED_DEFAULT_MINUTES):
    blocks = sync_blocks(db, task_id=task.id, items=[{
        "block_type": BLOCK_TIMED,
        "title": "Контрольная работа",
        "body": "Нарисуйте композицию",
        "is_required": True,
        "time_limit_minutes": minutes,
    }])
    db.commit()
    return blocks[0]


def _work(db, user):
    work = Work(
        user_id=user.id, work_type="mock_exam", month="сентябрь", year=TODAY.year,
        filename="control.jpg", s3_url="https://example.com/control.jpg",
    )
    db.add(work)
    db.flush()
    work.created_at = _utc(msk_midnight(TODAY) + timedelta(hours=12))
    db.commit()
    return work


# ── конструктор ─────────────────────────────────────────────────────────────

def test_limit_is_saved(db, regular_user):
    task = _task(db, regular_user)
    block = _timed(db, task, minutes=60)

    assert block.time_limit_minutes == 60


def test_limit_is_cleared_on_other_types(db, regular_user):
    """Блок переключили с работы на время на текст — лимит не должен остаться."""
    task = _task(db, regular_user)
    block = _timed(db, task)
    blocks = sync_blocks(db, task_id=task.id, items=[{
        "id": block.id, "block_type": "text", "title": "Текст", "body": "просто текст",
        "time_limit_minutes": 60,
    }])
    db.commit()

    assert blocks[0].time_limit_minutes is None


def test_timed_block_survives_without_body(db, regular_user):
    """Кнопка «Начать» самодостаточна, как и «Загрузить портфолио»."""
    task = _task(db, regular_user)
    blocks = sync_blocks(db, task_id=task.id, items=[{
        "block_type": BLOCK_TIMED, "title": None, "body": None,
        "time_limit_minutes": 60,
    }])
    db.commit()

    assert len(blocks) == 1


# ── старт ───────────────────────────────────────────────────────────────────

def test_start_marks_the_time(db, regular_user):
    task = _task(db, regular_user)
    block = _timed(db, task)

    state = start_timed_block(db, block=block, user_id=regular_user.id)
    db.commit()

    assert state.started_at is not None


def test_second_start_does_not_reset_the_clock(db, regular_user):
    """Отсчёт — предмет измерения: повторное «Начать» его не сдвигает."""
    task = _task(db, regular_user)
    block = _timed(db, task)
    first = start_timed_block(db, block=block, user_id=regular_user.id)
    db.commit()
    started_at = first.started_at

    again = start_timed_block(db, block=block, user_id=regular_user.id)
    db.commit()

    assert again.started_at == started_at


# ── превышение ──────────────────────────────────────────────────────────────

def test_no_overrun_without_start(db, regular_user):
    task = _task(db, regular_user)
    block = _timed(db, task)

    assert timed_overrun(block, None) is False


def test_within_the_limit_is_not_an_overrun(db, regular_user):
    task = _task(db, regular_user)
    block = _timed(db, task, minutes=60)
    state = TaskBlockState(
        block_id=block.id, user_id=regular_user.id, status="done",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=40),
        completed_at=datetime.now(timezone.utc),
    )

    assert timed_overrun(block, state) is False


def test_over_the_limit_is_marked(db, regular_user):
    """«Сколько детей превысили время… пометить красненьким»."""
    task = _task(db, regular_user)
    block = _timed(db, task, minutes=60)
    state = TaskBlockState(
        block_id=block.id, user_id=regular_user.id, status="done",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=95),
        completed_at=datetime.now(timezone.utc),
    )

    assert timed_overrun(block, state) is True


# ── сдача ───────────────────────────────────────────────────────────────────

def test_upload_closes_a_started_work(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    block = _timed(db, task)
    start_timed_block(db, block=block, user_id=regular_user.id)
    db.commit()
    _work(db, regular_user)

    steps = build_cycle_feed(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )

    assert steps[0]["status"] == "done"
    assert get_state(db, block_id=block.id, user_id=regular_user.id).completed_at is not None


def test_upload_without_start_does_not_close_the_work(db, regular_user):
    """Без нажатия «Начать» засчитывать нечего: не с чем сравнивать лимит."""
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    block = _timed(db, task)
    _work(db, regular_user)

    steps = build_cycle_feed(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )

    assert steps[0]["status"] == "current"
    assert get_state(db, block_id=block.id, user_id=regular_user.id) is None


# ── экраны ──────────────────────────────────────────────────────────────────

def test_start_endpoint_marks_the_time(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _timed(db, task)

    resp = client.post(f"/cabinet/tracker/blocks/{block.id}/start")

    assert resp.status_code == 200
    assert resp.json()["time_limit_minutes"] == TIMED_DEFAULT_MINUTES
    assert get_state(db, block_id=block.id, user_id=user.id).started_at is not None


def test_start_endpoint_404_for_other_block_types(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = TaskBlock(task_id=task.id, block_type="text", body="текст", sort_order=1)
    db.add(block)
    db.commit()

    assert client.post(f"/cabinet/tracker/blocks/{block.id}/start").status_code == 404


def test_constructor_offers_the_timed_block(admin_client):
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert 'data-add-block="timed"' in page.text
    assert "Работа на время" in page.text
