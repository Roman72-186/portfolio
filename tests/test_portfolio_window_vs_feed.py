"""Окно портфолио и лента обязаны одинаково решать, открыт ли блок.

Код-ревью 28.09.2026, P2 № 9. `portfolio_window.portfolio_windows` считает
доступ блока через `task_blocks.feed_state` — по цепочке блоков ОДНОГО
задания. Лента (`cycle_feed.build_cycle_feed`) склеивает блоки всех заданий
ленты в одну цепочку и выкидывает то, чего ученик не видит. Сценарии ниже
сравнивают два ответа на один и тот же блок.

На 29.09.2026 оба расходятся — `xfail(strict=True)`, правка ждёт решения
владельца (варианты — `reports/code-review-2026-09-28.md`, P2 № 9). Когда
расхождение починят, strict-xfail покраснеет: снять пометку.
"""
from datetime import timedelta, timezone

import pytest

from app.models.task_block import BLOCK_PORTFOLIO, BLOCK_QUESTION, BLOCK_UPLOAD, TaskBlock
from app.services.cycle_feed import build_cycle_feed
from app.services.portfolio_window import portfolio_windows
from app.services.program import day_bounds
from app.services.tracker import create_task
from app.services.tz import now_msk, today_msk

TODAY = today_msk()


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _task(db, owner, *, title, hour):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY)[0] + timedelta(hours=hour),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    return task


def _block(db, task, block_type, *, order, required=True, **fields):
    block = TaskBlock(
        task_id=task.id, block_type=block_type, title=f"Блок {order}",
        sort_order=order, is_required=required, **fields,
    )
    db.add(block)
    db.commit()
    return block


def _portfolio(db, task, *, order):
    return _block(
        db, task, BLOCK_PORTFOLIO, order=order,
        opens_at=_utc(now_msk() - timedelta(days=1)),
        closes_at=_utc(now_msk() + timedelta(days=1)),
    )


def _both(db, user, block):
    feed = build_cycle_feed(
        db, user_id=user.id, user_tariff=user.tariff, start=TODAY, end=TODAY,
    )
    feed_status = next(step["status"] for step in feed if step["block"] and step["block"].id == block.id)
    window = next(w for w in portfolio_windows(db, user_id=user.id, user_tariff=user.tariff) if w.block_id == block.id)
    return feed_status, window.is_open


@pytest.mark.xfail(strict=True, reason="P2 № 9: окно считает цепочку одного задания, лента — всей ленты")
def test_locked_in_feed_by_previous_task_is_closed_for_upload(db, regular_user):
    """Прямое направление из отчёта: обязательный несданный блок в задании
    выше по ленте. Лента запирает портфолио, окно считать его открытым не должно."""
    first = _task(db, regular_user, title="Сначала сдать", hour=6)
    _block(db, first, BLOCK_UPLOAD, order=1)
    second = _task(db, regular_user, title="Портфолио", hour=7)
    portfolio = _portfolio(db, second, order=1)

    feed_status, is_open = _both(db, regular_user, portfolio)

    assert feed_status == "locked"
    assert is_open is False


@pytest.mark.xfail(strict=True, reason="P2 № 9: feed_state не выкидывает вопросы hidden_until_done, лента выкидывает")
def test_hidden_question_does_not_close_upload_that_feed_shows_open(db, regular_user):
    """Обратное направление: обязательный вопрос «после закрытия задания»
    лента не показывает и в цепочку не берёт. Кнопка портфолио в ленте
    открыта — загрузка по ней тоже должна быть открыта."""
    task = _task(db, regular_user, title="Портфолио с вопросом", hour=6)
    _block(db, task, BLOCK_QUESTION, order=1, hidden_until_done=True, body="Как прошло?", question_type="text")
    portfolio = _portfolio(db, task, order=2)

    feed_status, is_open = _both(db, regular_user, portfolio)

    assert feed_status == "current"
    assert is_open is True
