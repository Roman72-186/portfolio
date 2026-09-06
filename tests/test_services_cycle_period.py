"""Цикл с произвольным периодом (app/services/tracker.py).

Решение владельца 06.09.2026 (голосовое 01:37): «открываем, устанавливаем, с
какого по какое это будет цикл — то есть он три недели». До этого период
учебной единицы был всегда календарной неделей, произвольный период выразить
было нечем.

План — plans/2026-09-06-apparchi-block-feed-replaces-week-tabs.md, этап 1.
"""
from datetime import date, timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.services.program import day_bounds, week_start
from app.services.tracker import (
    close_task_for_user,
    create_task,
    cycle_bounds,
    cycle_for_day,
    effective_cycle,
    is_cycle_complete,
)
from app.services.tz import msk_midnight, today_msk

# Цикл предобучения — три недели (18 сентября – 3 октября 2026). Даты берутся
# от сегодня, а не календарные: `accessible_topic_ids` отдаёт только уже
# открывшиеся темы, и цикл с датами в будущем ученику просто не виден.
TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=4)
CYCLE_MIDDLE = TODAY
CYCLE_END = TODAY + timedelta(days=11)


def _utc(value):
    """Момент в UTC без таймзоны — так его отдаёт SQLite в тестах.

    Колонка `DateTime(timezone=True)` на проде хранит момент честно, а SQLite
    таймзону теряет и возвращает наивное значение, которое весь проект
    трактует как UTC (`program.py::msk_date`). Значит и класть в неё нужно
    UTC, иначе тест проверяет не то, что случится на проде: 23:59 МСК,
    записанные наивно, прочитаются как 23:59 UTC и уедут на следующие сутки.
    """
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner, *, starts_on: date, ends_on: date | None, title="Цикл") -> LearningTopic:
    topic = LearningTopic(
        title=f"{title} {starts_on.isoformat()}",
        opens_at=_utc(msk_midnight(starts_on)),
        ends_at=None if ends_on is None
        else _utc(msk_midnight(ends_on) + timedelta(hours=23, minutes=59)),
        assign_to_all=True,
        is_published=True,
        kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _task(db, owner, *, due_on: date, kind="video", is_required=True):
    task = create_task(
        db, title=f"{kind}-{due_on.isoformat()}", user_id=owner.id,
        kind=kind, due_at=day_bounds(due_on)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=is_required,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


# ── cycle_bounds ────────────────────────────────────────────────────────────

def test_cycle_without_end_stays_a_calendar_week(db, regular_user):
    """Тема, заведённая до 06.09.2026, читается как раньше: неделя от понедельника."""
    wednesday = week_start(TODAY) + timedelta(days=2)
    topic = _cycle(db, regular_user, starts_on=wednesday, ends_on=None)

    monday = week_start(wednesday)
    assert cycle_bounds(topic) == (monday, monday + timedelta(days=6))


def test_cycle_with_end_keeps_its_own_period(db, regular_user):
    topic = _cycle(db, regular_user, starts_on=CYCLE_START, ends_on=CYCLE_END)

    assert cycle_bounds(topic) == (CYCLE_START, CYCLE_END)


def test_broken_period_collapses_to_the_start_day(db, regular_user):
    """Конец раньше начала — данные из базы, падать на них незачем."""
    topic = _cycle(db, regular_user, starts_on=CYCLE_END, ends_on=CYCLE_START)

    assert cycle_bounds(topic) == (CYCLE_END, CYCLE_END)


# ── cycle_for_day ───────────────────────────────────────────────────────────

def test_day_inside_the_period_finds_the_cycle(db, regular_user):
    topic = _cycle(db, regular_user, starts_on=CYCLE_START, ends_on=CYCLE_END)

    assert cycle_for_day(db, regular_user.id, CYCLE_MIDDLE).id == topic.id


def test_period_edges_are_inclusive(db, regular_user):
    topic = _cycle(db, regular_user, starts_on=CYCLE_START, ends_on=CYCLE_END)

    assert cycle_for_day(db, regular_user.id, CYCLE_START).id == topic.id
    assert cycle_for_day(db, regular_user.id, CYCLE_END).id == topic.id


def test_gap_between_cycles_gives_nothing(db, regular_user):
    """В зазоре резолвер молчит — что показать, решает экран."""
    _cycle(db, regular_user, starts_on=CYCLE_START, ends_on=CYCLE_START + timedelta(days=1))

    assert cycle_for_day(db, regular_user.id, CYCLE_MIDDLE) is None


def test_overlapping_cycles_resolve_to_the_later_one(db, regular_user):
    """Периоды, в отличие от понедельников, пересекаются — ответ определён."""
    _cycle(db, regular_user, starts_on=CYCLE_START, ends_on=CYCLE_END, title="Ранний")
    later = _cycle(db, regular_user, starts_on=CYCLE_START + timedelta(days=2),
                   ends_on=CYCLE_END, title="Поздний")

    assert cycle_for_day(db, regular_user.id, CYCLE_MIDDLE).id == later.id


# ── is_cycle_complete ───────────────────────────────────────────────────────

def test_required_task_inside_the_period_blocks_the_cycle(db, regular_user):
    topic = _cycle(db, regular_user, starts_on=CYCLE_START, ends_on=CYCLE_END)
    _task(db, regular_user, due_on=CYCLE_MIDDLE)

    assert is_cycle_complete(db, regular_user.id, topic) is False


def test_closed_task_lets_the_cycle_through(db, regular_user):
    topic = _cycle(db, regular_user, starts_on=CYCLE_START, ends_on=CYCLE_END)
    task = _task(db, regular_user, due_on=date(2026, 9, 30))
    close_task_for_user(db, task, regular_user.id, source="manual")
    db.commit()

    assert is_cycle_complete(db, regular_user.id, topic) is True


def test_task_outside_the_period_does_not_block_the_cycle(db, regular_user):
    """Трёхнедельный цикл считает только свои дни, а не календарную неделю."""
    topic = _cycle(db, regular_user, starts_on=CYCLE_START, ends_on=CYCLE_END)
    _task(db, regular_user, due_on=CYCLE_END + timedelta(days=20))

    assert is_cycle_complete(db, regular_user.id, topic) is True


def test_mock_exam_ticket_does_not_block_the_cycle(db, regular_user):
    """Билет Пробника запирает месяц, а не цикл (решение владельца 23.08)."""
    topic = _cycle(db, regular_user, starts_on=CYCLE_START, ends_on=CYCLE_END)
    _task(db, regular_user, due_on=date(2026, 9, 30), kind="mock_exam")

    assert is_cycle_complete(db, regular_user.id, topic) is True


# ── effective_cycle ─────────────────────────────────────────────────────────

def test_debtor_stays_on_the_unfinished_cycle(db, regular_user):
    """Должник видит свой застрявший цикл, а не тот, что идёт по календарю."""
    today = today_msk()
    stuck = _cycle(db, regular_user, starts_on=today - timedelta(days=30),
                   ends_on=today - timedelta(days=16), title="Прошлый")
    _cycle(db, regular_user, starts_on=today - timedelta(days=3),
           ends_on=today + timedelta(days=10), title="Текущий")
    _task(db, regular_user, due_on=today - timedelta(days=20))

    assert effective_cycle(db, regular_user.id, today).id == stuck.id


def test_no_debt_gives_the_current_cycle(db, regular_user):
    today = today_msk()
    _cycle(db, regular_user, starts_on=today - timedelta(days=30),
           ends_on=today - timedelta(days=16), title="Прошлый")
    current = _cycle(db, regular_user, starts_on=today - timedelta(days=3),
                     ends_on=today + timedelta(days=10), title="Текущий")

    assert effective_cycle(db, regular_user.id, today).id == current.id


def test_future_cycle_is_not_handed_out_early(db, regular_user):
    today = today_msk()
    _cycle(db, regular_user, starts_on=today + timedelta(days=7),
           ends_on=today + timedelta(days=21), title="Будущий")

    assert effective_cycle(db, regular_user.id, today) is None
