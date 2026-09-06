"""Блок «Загрузить портфолио» (владелец 03.09.2026).

«Добавить кнопку "загрузить портфолио" — эта кнопка перенесёт сразу на готовый
наш функционал… только здесь нужно сделать так, что он обязан загрузить это
портфолио: пока не будет подтверждения, что он загрузил портфолио, которое
именно 18 числа, у него не откроется актуальное образовательное пространство
дальше.»

Своего хранилища у блока нет: он ведёт на существующий экран загрузки работ и
закрывается фактом загрузки, а не галочкой ученика.
"""
from datetime import timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import BLOCK_PORTFOLIO, BLOCK_TEXT, TaskBlock
from app.models.work import Work
from app.services.cycle_feed import build_cycle_feed, has_portfolio_upload
from app.services.program import day_bounds
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=2)
CYCLE_END = TODAY + timedelta(days=12)


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    topic = LearningTopic(
        title="Цикл", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    return topic


def _task(db, owner, *, title="Первый день", due_on=TODAY):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(due_on)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _portfolio_block(db, task, order=1):
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_PORTFOLIO,
        title="Загрузите портфолио", sort_order=order, is_required=True,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _work(db, user, *, days_ago=0):
    work = Work(
        user_id=user.id, work_type="before", month="сентябрь", year=TODAY.year,
        filename="work.jpg", s3_url="https://example.com/work.jpg",
    )
    db.add(work)
    db.flush()
    work.created_at = _utc(
        msk_midnight(TODAY - timedelta(days=days_ago)) + timedelta(hours=12)
    )
    db.commit()
    return work


def _feed(db, user):
    return build_cycle_feed(
        db, user_id=user.id, user_tariff=user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )


# ── факт загрузки ───────────────────────────────────────────────────────────

def test_no_upload_means_not_done(db, regular_user):
    assert has_portfolio_upload(db, regular_user.id, since=CYCLE_START) is False


def test_upload_inside_the_period_counts(db, regular_user):
    _work(db, regular_user)

    assert has_portfolio_upload(db, regular_user.id, since=CYCLE_START) is True


def test_old_upload_does_not_count(db, regular_user):
    """«Портфолио, которое именно 18 числа» — прошлогодняя работа не закрывает
    сегодняшний шаг."""
    _work(db, regular_user, days_ago=90)

    assert has_portfolio_upload(db, regular_user.id, since=CYCLE_START) is False


# ── поведение в ленте ───────────────────────────────────────────────────────

def test_portfolio_step_is_open_until_the_work_is_uploaded(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    _portfolio_block(db, task)

    steps = _feed(db, regular_user)

    assert steps[0]["status"] == "current"


def test_portfolio_step_closes_itself_after_upload(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    _portfolio_block(db, task)
    _work(db, regular_user)

    steps = _feed(db, regular_user)

    assert steps[0]["status"] == "done"


def test_portfolio_step_blocks_the_rest_of_the_feed(db, regular_user):
    """«Пока не загрузит — у него не откроется образовательное пространство
    дальше»."""
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    _portfolio_block(db, task, order=1)
    db.add(TaskBlock(
        task_id=task.id, block_type=BLOCK_TEXT, title="Правила",
        body="текст правил", sort_order=2, is_required=True,
    ))
    db.commit()

    steps = _feed(db, regular_user)

    assert [s["status"] for s in steps] == ["current", "locked"]


def test_upload_opens_the_rest_of_the_feed(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    _portfolio_block(db, task, order=1)
    db.add(TaskBlock(
        task_id=task.id, block_type=BLOCK_TEXT, title="Правила",
        body="текст правил", sort_order=2, is_required=True,
    ))
    db.commit()
    _work(db, regular_user)

    steps = _feed(db, regular_user)

    assert [s["status"] for s in steps] == ["done", "current"]


# ── конструктор ─────────────────────────────────────────────────────────────

def test_constructor_offers_the_portfolio_block(admin_client):
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert 'data-add-block="portfolio"' in page.text
    assert "Загрузить портфолио" in page.text


def test_portfolio_block_saves_without_content(admin_client, db):
    """Кнопка самодостаточна: без заголовка и пояснения блок всё равно нужен."""
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    resp = client.post(f"/cabinet/staff/program/{day}/material", json={
        "title": "Первый день",
        "description": None,
        "subject": None,
        "is_required": True,
        "starts_on": None,
        "blocks": [{"block_type": "portfolio", "title": None, "body": None,
                    "is_required": True, "subject": None, "tariffs": [],
                    "opens_at": None, "bypass_sequence": False}],
        "audience": {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    })

    assert resp.status_code == 200
    block = db.query(TaskBlock).filter(TaskBlock.block_type == BLOCK_PORTFOLIO).first()
    assert block is not None
    assert block.is_required is True
