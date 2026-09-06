"""Единая лента заданий цикла — то, что ученик видит вместо восьми вкладок.

Владелец 06.09.2026 (голосовые 01:37 и 01:38): «мне смысл эти восемь кнопок
держать? Просто открываем, устанавливаем, с какого по какое это будет цикл,
расставляем блоки друг за другом по порядку… после сохранения в таком же виде
появляется у ученика, уже с учётом доступности». План —
`plans/2026-09-06-apparchi-block-feed-replaces-week-tabs.md`, этап 2.

Чем лента отличается от снесённых вкладок недели (`build_week_tabs`, убрана
06.09.2026 вместе с `WEEK_TAB_SEQUENCE`):

- **порядок задаёт преподаватель, а не вид задания.** Вкладки шли фиксированной
  восьмёркой видов, лента идёт по `due_at` и `sort_order` — как расставили в
  конструкторе;
- **шаг ленты — блок, а не вкладка.** Последовательная блокировка считается
  сквозь задачи: обязательный незакрытый блок в первом задании запирает всё
  ниже, включая блоки следующих заданий;
- **окно — период цикла**, а не понедельник плюс шесть дней.

**Задача без блоков тоже шаг.** Элементы, заведённые до универсального
конструктора (видео, домашка, пробник), содержимого в `task_blocks` не имеют, и
без такого шага они просто исчезли бы из ленты вместе с домашками, которые
ученики уже сдают.
"""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.learning_topic import LearningTopic
from app.models.tracker import ITEM_MOCK_EXAM, STATUS_DONE
from app.services.program import day_bounds
from app.services.task_blocks import (
    get_blocks_for_tasks,
    get_states,
    get_tariffs,
    is_block_accessible,
)
from app.services.tracker import (
    accessible_task_entries,
    cycle_bounds,
    effective_cycle,
    effective_week_start,
)

STATUS_LOCKED = "locked"
STATUS_CURRENT = "current"


def feed_window(
    db: Session, user_id: int, today: date
) -> tuple[LearningTopic | None, date, date]:
    """Цикл ученика и границы ленты в московских датах, включительно.

    Цикла может не быть вовсе: экрана, на котором человек заводит цикл, до
    этапа 3 нет, а на проде живут ученики с задачами и без единой темы. Тогда
    окно падает на календарную неделю по прежнему правилу
    (`effective_week_start`) — ровно то поведение, ради которого 25.08.2026
    убрали баннер «Пока нет ни одной доступной недели»: задачи у ученика есть,
    и он должен их видеть, даже когда рамки вокруг них никто не завёл.
    """
    topic = effective_cycle(db, user_id, today)
    if topic is not None:
        first, last = cycle_bounds(topic)
        return topic, first, last
    monday = effective_week_start(db, user_id, today)
    return None, monday, monday + timedelta(days=6)


def _task_done(entry: dict) -> bool:
    return entry["status"] == STATUS_DONE


def build_cycle_feed(
    db: Session, *, user_id: int, user_tariff: str | None, start: date, end: date
) -> list[dict]:
    """Шаги ленты за период `[start, end]`, сверху вниз, со статусом ученика.

    Шаг — словарь `{"block", "task", "status", "state", "entry"}`. `block` —
    `None` у задачи без блоков: она идёт в ленте одной карточкой и закрывается
    так же, как закрывалась во вкладках.

    Статус: `"done"` — закрыт, `"current"` — можно делать сейчас, `"locked"` —
    ждёт того, что выше. Блокировка считается сквозной: список блоков всех
    задач склеивается в один и отдаётся `is_block_accessible`, который уже
    умеет три условия разом (период доступа, тариф, последовательность).

    Билет Пробника (`ITEM_MOCK_EXAM`) в ленте показывается, но хвост не
    запирает — он блокирует месяц, а не цикл (решение владельца 23.08,
    подтверждено 24.08). Ради этого его собственная обязательность в
    последовательности игнорируется.
    """
    window_start, _ = day_bounds(start)
    _, window_end = day_bounds(end)
    entries = accessible_task_entries(db, user_id, start=window_start, end=window_end)
    if not entries:
        return []

    tasks = [entry["task"] for entry in entries]
    blocks_by_task = get_blocks_for_tasks(db, [task.id for task in tasks])

    # Сквозной список блоков в порядке ленты — на нём и считается блокировка.
    ordered_blocks = []
    for entry in entries:
        ordered_blocks.extend(blocks_by_task.get(entry["task"].id, []))
    block_ids = [block.id for block in ordered_blocks]
    states = get_states(db, block_ids=block_ids, user_id=user_id)
    tariffs_by_block = get_tariffs(db, block_ids)

    # Задача без блоков участвует в блокировке хвоста наравне с блоками: пока
    # обязательное видео не досмотрено, следующее задание ленты закрыто.
    # `blocked` взводится один раз и дальше запирает всё, что ниже.
    blocked = False
    steps: list[dict] = []
    block_index = 0
    for entry in entries:
        task = entry["task"]
        task_blocks = blocks_by_task.get(task.id, [])
        if not task_blocks:
            done = _task_done(entry)
            steps.append({
                "task": task,
                "block": None,
                "state": None,
                "entry": entry,
                "status": STATUS_DONE if done else (STATUS_LOCKED if blocked else STATUS_CURRENT),
            })
            if (
                not done
                and task.is_required
                and task.kind != ITEM_MOCK_EXAM
            ):
                blocked = True
            continue

        for block in task_blocks:
            state = states.get(block.id)
            done = state is not None and state.status == STATUS_DONE
            accessible = not blocked and is_block_accessible(
                block_index=block_index,
                blocks=ordered_blocks,
                states=states,
                tariffs_by_block=tariffs_by_block,
                user_tariff=user_tariff,
            )
            steps.append({
                "task": task,
                "block": block,
                "state": state,
                "entry": entry,
                "status": STATUS_DONE if done else (STATUS_CURRENT if accessible else STATUS_LOCKED),
            })
            block_index += 1
    return steps


def feed_for_student(
    db: Session, *, user_id: int, user_tariff: str | None, today: date
) -> dict:
    """Готовая лента для экрана: цикл, его границы и шаги.

    Одна точка входа для роута — чтобы экран не собирал окно и шаги по
    отдельности и не разъезжался с тем, по какому периоду считается закрытие
    цикла.
    """
    topic, start, end = feed_window(db, user_id, today)
    steps = build_cycle_feed(
        db, user_id=user_id, user_tariff=user_tariff, start=start, end=end
    )
    return {
        "topic": topic,
        "start": start,
        "end": end,
        "steps": steps,
        "done_count": sum(1 for step in steps if step["status"] == STATUS_DONE),
        "total_count": len(steps),
    }
