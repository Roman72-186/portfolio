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
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.learning_topic import LearningTopic
from app.models.task_block import BLOCK_PORTFOLIO
from app.models.tracker import ITEM_MOCK_EXAM, STATUS_DONE
from app.models.work import Work
from app.services.program import day_bounds, msk_date
from app.services.task_blocks import (
    close_block_for_user,
    get_blocks_for_tasks,
    get_states,
    get_tariffs,
    is_block_accessible,
)
from app.services.tracker import (
    accessible_cycles,
    accessible_task_entries,
    cycle_bounds,
    effective_cycle,
    effective_week_start,
)

STATUS_LOCKED = "locked"
STATUS_CURRENT = "current"

# Почему шаг заперт: очередью или календарём. Ученику это разные сообщения —
# «сделай предыдущее» против «откроется 23 сентября» (владелец 03.09.2026).
LOCK_BY_SEQUENCE = "sequence"
LOCK_BY_DATE = "date"


def _not_open_yet(value: datetime | None, now: datetime) -> bool:
    """Момент открытия ещё не наступил. `None` — открыто всегда."""
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value > now


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


def has_portfolio_upload(db: Session, user_id: int, *, since: date) -> bool:
    """Загружал ли ученик работу начиная с `since` (московская дата).

    Блок «Загрузить портфолио» закрывается фактом загрузки, а не галочкой
    (владелец 03.09.2026: «пока не будет подтверждения, что он загрузил
    портфолио, которое именно 18 числа, у него не откроется актуальное
    образовательное пространство дальше»). Отсчёт — от начала цикла: прошлогодняя
    работа не должна засчитывать сегодняшнее задание.
    """
    start, _ = day_bounds(since)
    return (
        db.query(Work.id)
        .filter(Work.user_id == user_id, Work.created_at >= start)
        .first()
        is not None
    )


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

    now = datetime.now(timezone.utc)

    tasks = [entry["task"] for entry in entries]
    blocks_by_task = get_blocks_for_tasks(db, [task.id for task in tasks])

    # Сквозной список блоков в порядке ленты — на нём и считается блокировка.
    ordered_blocks = []
    for entry in entries:
        ordered_blocks.extend(blocks_by_task.get(entry["task"].id, []))
    block_ids = [block.id for block in ordered_blocks]
    states = get_states(db, block_ids=block_ids, user_id=user_id)
    tariffs_by_block = get_tariffs(db, block_ids)

    # Блок «Загрузить портфолио» закрывается фактом загрузки работы, а не
    # галочкой ученика (владелец 03.09.2026). Закрываем по-настоящему, а не
    # только в отрисовке: иначе закрытый на экране блок продолжал бы запирать
    # всё, что ниже, — последовательность считается по состояниям блоков. Тот
    # же приём, что у видео (`api/video.py::_close_video_task_once`).
    pending_portfolio = [
        block for block in ordered_blocks
        if block.block_type == BLOCK_PORTFOLIO and block.id not in states
    ]
    if pending_portfolio and has_portfolio_upload(db, user_id, since=start):
        for block in pending_portfolio:
            close_block_for_user(
                db, block=block, user_id=user_id, source="portfolio_upload"
            )
        db.commit()
        states = get_states(db, block_ids=block_ids, user_id=user_id)

    # Задача без блоков участвует в блокировке хвоста наравне с блоками: пока
    # обязательное видео не досмотрено, следующее задание ленты закрыто.
    # `blocked` взводится один раз и дальше запирает всё, что ниже.
    blocked = False
    steps: list[dict] = []
    block_index = 0
    for entry in entries:
        task = entry["task"]
        task_blocks = blocks_by_task.get(task.id, [])
        # Дата открытия задания целиком: «теория и задания откроются только с
        # 23 сентября 00:00» — независимо от того, что ученик успел сделать
        # раньше (владелец 03.09.2026). Складывается с очередью, не заменяет.
        task_waits_date = _not_open_yet(task.starts_at, now)
        opens_on = msk_date(task.starts_at) if task_waits_date else None

        if not task_blocks:
            done = _task_done(entry)
            if done:
                status, lock_reason = STATUS_DONE, None
            elif task_waits_date:
                status, lock_reason = STATUS_LOCKED, LOCK_BY_DATE
            elif blocked:
                status, lock_reason = STATUS_LOCKED, LOCK_BY_SEQUENCE
            else:
                status, lock_reason = STATUS_CURRENT, None
            steps.append({
                "task": task,
                "block": None,
                "state": None,
                "entry": entry,
                "status": status,
                "lock_reason": lock_reason,
                "opens_on": opens_on,
                "subject": task.subject,
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
            block_waits_date = _not_open_yet(block.opens_at, now)
            accessible = (
                not blocked
                and not task_waits_date
                and is_block_accessible(
                    block_index=block_index,
                    blocks=ordered_blocks,
                    states=states,
                    tariffs_by_block=tariffs_by_block,
                    user_tariff=user_tariff,
                    now=now,
                )
            )
            if done:
                status, lock_reason = STATUS_DONE, None
            elif accessible:
                status, lock_reason = STATUS_CURRENT, None
            elif task_waits_date or block_waits_date:
                status, lock_reason = STATUS_LOCKED, LOCK_BY_DATE
            else:
                status, lock_reason = STATUS_LOCKED, LOCK_BY_SEQUENCE
            steps.append({
                "task": task,
                "block": block,
                "state": state,
                "entry": entry,
                "status": status,
                "lock_reason": lock_reason,
                # Дата блока важнее даты задания: она ближе к тому, что ученик
                # видит перед собой.
                "opens_on": (
                    msk_date(block.opens_at) if block_waits_date else opens_on
                ),
                # Предмет блока важнее предмета задания: часть цикла идёт без
                # деления на Рисунок и Композицию, часть — с делением
                # (владелец 03.09.2026).
                "subject": block.subject or task.subject,
            })
            block_index += 1
    return steps


def started_cycles(db: Session, user_id: int, today: date) -> list[LearningTopic]:
    """Циклы ученика, которые уже начались, от поздних к ранним.

    Нужны для возврата в пройденное: «он может вернуться в этот цикл, зайти в
    этот цикл, потому что у каждого цикла своя тема в обучении» (владелец
    03.09.2026). Не начавшиеся не показываем — программа вперёд не выдаётся.
    """
    started = [
        topic for topic in accessible_cycles(db, user_id)
        if cycle_bounds(topic)[0] <= today
    ]
    return list(reversed(started))


def feed_for_student(
    db: Session, *, user_id: int, user_tariff: str | None, today: date,
    cycle_id: int | None = None,
) -> dict:
    """Готовая лента для экрана: цикл, его границы и шаги.

    Одна точка входа для роута — чтобы экран не собирал окно и шаги по
    отдельности и не разъезжался с тем, по какому периоду считается закрытие
    цикла.

    `cycle_id` — открыть конкретный цикл вместо текущего: ученик возвращается в
    пройденное. Чужой или ещё не начавшийся цикл молча игнорируется — падать на
    подобранном в адресной строке номере незачем.
    """
    current_topic, current_start, current_end = feed_window(db, user_id, today)
    chosen = None
    if cycle_id is not None:
        chosen = next(
            (t for t in started_cycles(db, user_id, today) if t.id == cycle_id), None
        )
    if chosen is not None:
        start, end = cycle_bounds(chosen)
        topic = chosen
    else:
        topic, start, end = current_topic, current_start, current_end
    steps = build_cycle_feed(
        db, user_id=user_id, user_tariff=user_tariff, start=start, end=end
    )
    cycles = started_cycles(db, user_id, today)
    return {
        "topic": topic,
        "start": start,
        "end": end,
        "steps": steps,
        # Список пройденных циклов для возврата; текущий помечен отдельно.
        "cycles": [
            {
                "id": item.id,
                "title": item.title,
                "start": cycle_bounds(item)[0],
                "end": cycle_bounds(item)[1],
                "is_current": topic is not None and item.id == topic.id,
            }
            for item in cycles
        ],
        # Открыт прошлый цикл, а не тот, на котором ученик стоит сейчас:
        # экран показывает его только для чтения.
        "is_archive": (
            chosen is not None
            and (current_topic is None or chosen.id != current_topic.id)
        ),
        "done_count": sum(1 for step in steps if step["status"] == STATUS_DONE),
        "total_count": len(steps),
        # Переключатель «Рисунок / Композиция» показывается, только когда в
        # цикле реально есть деление по предметам (владелец 03.09.2026: «в
        # предыдущих циклах эти кнопки не нужны, мы просто не будем ставить
        # разделение, и кнопок в принципе не будет»).
        "subjects": sorted({
            step["subject"] for step in steps if step["subject"]
        }),
    }
