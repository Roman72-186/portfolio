"""Единая лента заданий цикла (app/services/cycle_feed.py).

Владелец 06.09.2026: восемь вкладок недели заменяются одной лентой, в которой
следующий шаг закрыт, пока не сделан предыдущий. Здесь проверяется то, чего
вкладки не умели: сквозная блокировка через границу задания и работа ленты
без заведённого цикла.

План — plans/2026-09-06-apparchi-block-feed-replaces-week-tabs.md, этап 2.
"""
from datetime import timedelta

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import BLOCK_TEXT, TaskBlock
from app.services.cycle_feed import build_cycle_feed, feed_for_student, feed_window
from app.services.program import day_bounds
from app.services.task_blocks import close_block_for_user
from app.services.tracker import close_task_for_user, create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=4)
CYCLE_END = TODAY + timedelta(days=11)


def _utc(value):
    """Момент в UTC без таймзоны: SQLite в тестах таймзону теряет (см.
    tests/test_services_cycle_period.py)."""
    from datetime import timezone
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner, *, starts_on=CYCLE_START, ends_on=CYCLE_END, title="Цикл"):
    topic = LearningTopic(
        title=title,
        opens_at=_utc(msk_midnight(starts_on)),
        ends_at=_utc(msk_midnight(ends_on) + timedelta(hours=23, minutes=59)),
        assign_to_all=True,
        is_published=True,
        kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _task(db, owner, *, title, due_on=TODAY, kind="material", is_required=True, order=0):
    task = create_task(
        db, title=title, user_id=owner.id, kind=kind,
        due_at=day_bounds(due_on)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=is_required,
    )
    task.is_published = True
    task.sort_order = order
    db.commit()
    db.refresh(task)
    return task


def _block(db, task, *, title, order=0, is_required=True):
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_TEXT, title=title,
        body="текст", sort_order=order, is_required=is_required,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _feed(db, user):
    return build_cycle_feed(
        db, user_id=user.id, user_tariff=user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )


# ── окно ленты ──────────────────────────────────────────────────────────────

def test_window_follows_the_cycle_period(db, regular_user):
    topic = _cycle(db, regular_user)

    found, start, end = feed_window(db, regular_user.id, TODAY)

    assert found.id == topic.id
    assert (start, end) == (CYCLE_START, CYCLE_END)


def test_without_a_cycle_window_falls_back_to_the_week(db, regular_user):
    """Цикла может не быть вовсе — ученик всё равно должен видеть свои задачи
    (то же решение, по которому 25.08 убрали баннер «нет доступной недели»)."""
    topic, start, end = feed_window(db, regular_user.id, TODAY)

    assert topic is None
    assert (end - start).days == 6


# ── задания без даты внутри цикла (владелец 10.09.2026) ────────────────────
# Заводятся не через _task() (тот всегда ставит due_at) — topic_id указывает
# прямо на цикл, как это делает create_cycle_material_item в cabinet_program.py.

def _undated_task(db, topic, owner, *, title, order=0, is_required=True):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=None, topic_id=topic.id, assign_to_all=True,
        is_required=is_required, sort_order=order,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def test_undated_cycle_tasks_appear_in_sort_order(db, regular_user):
    topic = _cycle(db, regular_user)
    _undated_task(db, topic, regular_user, title="Второе", order=1)
    _undated_task(db, topic, regular_user, title="Первое", order=0)

    result = feed_for_student(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff, today=TODAY,
    )

    titles = [step["task"].title for step in result["steps"]]
    assert titles == ["Первое", "Второе"]


def test_undated_task_from_another_cycle_does_not_leak_in(db, regular_user):
    """`topic_id` в accessible_task_entries сужает бездатную выборку строго
    до одного цикла — без него чужой недоделанный бездатный шаг блокировал
    бы не тот цикл (найдено при разработке, было регрессией)."""
    topic_a = _cycle(db, regular_user, starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=2), title="A")
    topic_b = _cycle(db, regular_user, starts_on=TODAY + timedelta(days=20), ends_on=TODAY + timedelta(days=25), title="B")
    _undated_task(db, topic_a, regular_user, title="Из цикла A")
    _undated_task(db, topic_b, regular_user, title="Из цикла B")

    result = feed_for_student(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff, today=TODAY,
    )

    titles = [step["task"].title for step in result["steps"]]
    assert titles == ["Из цикла A"]


def test_undated_required_task_locks_the_next_one(db, regular_user):
    topic = _cycle(db, regular_user)
    _undated_task(db, topic, regular_user, title="Первое", order=0, is_required=True)
    _undated_task(db, topic, regular_user, title="Второе", order=1)

    result = feed_for_student(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff, today=TODAY,
    )

    assert [step["status"] for step in result["steps"]] == ["current", "locked"]


# ── порядок и сквозная блокировка ───────────────────────────────────────────

def test_blocks_follow_the_order_the_teacher_set(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Задание")
    _block(db, task, title="Второй", order=2)
    _block(db, task, title="Первый", order=1)

    titles = [step["block"].title for step in _feed(db, regular_user)]

    assert titles == ["Первый", "Второй"]


def test_required_block_locks_everything_below_it(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Задание")
    _block(db, task, title="Первый", order=1)
    _block(db, task, title="Второй", order=2)

    steps = _feed(db, regular_user)

    assert [step["status"] for step in steps] == ["current", "locked"]


def test_lock_carries_across_the_task_boundary(db, regular_user):
    """Главное отличие от вкладок: обязательный блок первого задания запирает
    блоки следующего, а не только собственную вкладку."""
    _cycle(db, regular_user)
    first = _task(db, regular_user, title="Первое", due_on=TODAY - timedelta(days=1))
    second = _task(db, regular_user, title="Второе", due_on=TODAY)
    _block(db, first, title="Блок первого", order=1)
    _block(db, second, title="Блок второго", order=1)

    steps = _feed(db, regular_user)

    assert [step["status"] for step in steps] == ["current", "locked"]


def test_closing_the_first_block_opens_the_next(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Задание")
    first = _block(db, task, title="Первый", order=1)
    _block(db, task, title="Второй", order=2)
    close_block_for_user(db, block=first, user_id=regular_user.id, source="manual")
    db.commit()

    steps = _feed(db, regular_user)

    assert [step["status"] for step in steps] == ["done", "current"]


def test_optional_block_does_not_lock_the_tail(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Задание")
    _block(db, task, title="Необязательный", order=1, is_required=False)
    _block(db, task, title="Следующий", order=2)

    steps = _feed(db, regular_user)

    assert [step["status"] for step in steps] == ["current", "current"]


# ── задачи без блоков ───────────────────────────────────────────────────────

def test_task_without_blocks_is_a_step_of_its_own(db, regular_user):
    """Видео и домашки, заведённые до конструктора блоков, из ленты не пропадают."""
    _cycle(db, regular_user)
    _task(db, regular_user, title="Видео недели", kind="video")

    steps = _feed(db, regular_user)

    assert len(steps) == 1
    assert steps[0]["block"] is None
    assert steps[0]["task"].title == "Видео недели"


def test_unfinished_task_without_blocks_locks_the_tail(db, regular_user):
    _cycle(db, regular_user)
    _task(db, regular_user, title="Видео", kind="video", due_on=TODAY - timedelta(days=1))
    later = _task(db, regular_user, title="Материал", due_on=TODAY)
    _block(db, later, title="Блок", order=1)

    steps = _feed(db, regular_user)

    assert [step["status"] for step in steps] == ["current", "locked"]


def test_closed_task_without_blocks_lets_the_tail_through(db, regular_user):
    _cycle(db, regular_user)
    video = _task(db, regular_user, title="Видео", kind="video", due_on=TODAY - timedelta(days=1))
    later = _task(db, regular_user, title="Материал", due_on=TODAY)
    _block(db, later, title="Блок", order=1)
    close_task_for_user(db, video, regular_user.id, source="manual")
    db.commit()

    steps = _feed(db, regular_user)

    assert [step["status"] for step in steps] == ["done", "current"]


def test_mock_exam_ticket_does_not_lock_the_tail(db, regular_user):
    """Билет Пробника блокирует месяц, а не цикл (решение владельца 23.08)."""
    _cycle(db, regular_user)
    _task(db, regular_user, title="Пробник", kind="mock_exam", due_on=TODAY - timedelta(days=1))
    later = _task(db, regular_user, title="Материал", due_on=TODAY)
    _block(db, later, title="Блок", order=1)

    steps = _feed(db, regular_user)

    assert [step["status"] for step in steps] == ["current", "current"]


# ── сводка для экрана ───────────────────────────────────────────────────────

def test_feed_for_student_counts_progress(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Задание")
    first = _block(db, task, title="Первый", order=1)
    _block(db, task, title="Второй", order=2)
    close_block_for_user(db, block=first, user_id=regular_user.id, source="manual")
    db.commit()

    feed = feed_for_student(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff, today=TODAY
    )

    assert (feed["done_count"], feed["total_count"]) == (1, 2)
    assert feed["topic"] is not None


def test_empty_cycle_gives_an_empty_feed(db, regular_user):
    _cycle(db, regular_user)

    feed = feed_for_student(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff, today=TODAY
    )

    assert feed["steps"] == []
    assert feed["total_count"] == 0


# ── дата открытия (владелец 03.09.2026) ─────────────────────────────────────

def test_task_with_future_start_date_is_locked_by_calendar(db, regular_user):
    """«Даже если ребёнок выполнил опрос 22 сентября в 20:00, теория и задания
    откроются только с 23 сентября 00:00»."""
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Теория")
    task.starts_at = _utc(msk_midnight(TODAY + timedelta(days=2)))
    db.commit()
    _block(db, task, title="Видео теории", order=1)

    steps = _feed(db, regular_user)

    assert steps[0]["status"] == "locked"
    assert steps[0]["lock_reason"] == "date"
    assert steps[0]["opens_on"] == TODAY + timedelta(days=2)


def test_past_start_date_does_not_lock(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Теория")
    task.starts_at = _utc(msk_midnight(TODAY - timedelta(days=1)))
    db.commit()
    _block(db, task, title="Видео теории", order=1)

    assert _feed(db, regular_user)[0]["status"] == "current"


def test_block_with_future_open_date_is_locked_by_calendar(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Задание")
    first = _block(db, task, title="Доступный", order=1, is_required=False)
    later = _block(db, task, title="Ждёт даты", order=2, is_required=False)
    later.opens_at = _utc(msk_midnight(TODAY + timedelta(days=3)))
    db.commit()

    steps = _feed(db, regular_user)

    assert steps[0]["status"] == "current"
    assert steps[1]["status"] == "locked"
    assert steps[1]["lock_reason"] == "date"
    assert first.id != later.id


# ── закрытие по календарю (владелец 10.09.2026) ─────────────────────────────

def test_block_with_past_close_date_is_locked_as_closed(db, regular_user):
    """«27 сентября в 23:30 закрывается доступ» — заперт по-своему, не как
    ожидание открытия и не как очередь."""
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Модуль")
    block = _block(db, task, title="Закрывается", order=1, is_required=False)
    block.closes_at = _utc(msk_midnight(TODAY - timedelta(days=1)))
    db.commit()

    steps = _feed(db, regular_user)

    assert steps[0]["status"] == "locked"
    assert steps[0]["lock_reason"] == "closed"


def test_locked_message_overrides_default_and_is_not_used_when_absent(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Модуль")
    with_message = _block(db, task, title="С текстом", order=1, is_required=False)
    with_message.closes_at = _utc(msk_midnight(TODAY - timedelta(days=1)))
    with_message.locked_message = "Пока проверь чат-комьюнити в телеграмме."
    without_message = _block(db, task, title="Без текста", order=2, is_required=False)
    without_message.closes_at = _utc(msk_midnight(TODAY - timedelta(days=1)))
    db.commit()

    steps = _feed(db, regular_user)

    assert steps[0]["block"].locked_message == "Пока проверь чат-комьюнити в телеграмме."
    assert steps[1]["block"].locked_message is None


def test_closed_required_block_does_not_lock_the_tail(db, regular_user):
    """Обязательный блок, который сам уже закрылся по календарю, не должен
    запирать хвост ленты навсегда — тот же тупик без выхода, что решён для
    тарифа (is_block_accessible)."""
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Модуль")
    expired = _block(db, task, title="Просрочено", order=1)
    expired.closes_at = _utc(msk_midnight(TODAY - timedelta(days=1)))
    _block(db, task, title="Дальше", order=2, is_required=False)
    db.commit()

    steps = _feed(db, regular_user)

    assert steps[0]["lock_reason"] == "closed"
    assert steps[1]["status"] == "current"


def test_closed_block_does_not_leak_into_waiting_for_banner(db, regular_user):
    """Закрытый навсегда блок не должен подсказывать несуществующую дату
    открытия в баннере «Следующее задание откроется …»."""
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Модуль")
    block = _block(db, task, title="Закрыт", order=1)
    block.closes_at = _utc(msk_midnight(TODAY - timedelta(days=1)))
    db.commit()

    feed = feed_for_student(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff, today=TODAY
    )

    assert feed["waiting_for"] is None


def test_sequence_lock_keeps_its_own_reason(db, regular_user):
    """Заперто очередью, а не календарём — подпись у ученика другая."""
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Задание")
    _block(db, task, title="Первый", order=1)
    _block(db, task, title="Второй", order=2)

    steps = _feed(db, regular_user)

    assert steps[1]["lock_reason"] == "sequence"


# ── «на сегодня всё» (владелец 03.09.2026) ──────────────────────────────────

def test_waiting_for_appears_when_everything_open_is_done(db, regular_user):
    """«Ему выпадает уведомление, что следующее задание откроется 23 сентября»."""
    _cycle(db, regular_user)
    today_task = _task(db, regular_user, title="Сегодня")
    done_block = _block(db, today_task, title="Опрос", order=1)
    close_block_for_user(db, block=done_block, user_id=regular_user.id, source="manual")
    later = _task(db, regular_user, title="Послезавтра", due_on=TODAY + timedelta(days=2))
    later.starts_at = _utc(msk_midnight(TODAY + timedelta(days=2)))
    db.commit()
    _block(db, later, title="Теория", order=1)

    feed = feed_for_student(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff, today=TODAY
    )

    assert feed["waiting_for"] == TODAY + timedelta(days=2)


def test_no_waiting_hint_while_something_is_doable(db, regular_user):
    """Есть что делать сейчас — подсказка только отвлекала бы."""
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Сегодня")
    _block(db, task, title="Опрос", order=1)

    feed = feed_for_student(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff, today=TODAY
    )

    assert feed["waiting_for"] is None


def test_no_waiting_hint_when_blocked_by_own_debt(db, regular_user):
    """Заперто собственным незакрытым шагом, а не календарём."""
    _cycle(db, regular_user)
    task = _task(db, regular_user, title="Задание")
    _block(db, task, title="Первый", order=1)
    _block(db, task, title="Второй", order=2)

    feed = feed_for_student(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff, today=TODAY
    )

    assert feed["waiting_for"] is None
