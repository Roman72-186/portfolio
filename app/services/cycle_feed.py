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
from app.models.work import WORK_TYPE_BEFORE, Work
from app.services.program import day_bounds, msk_date
from app.services.task_blocks import (
    close_block_for_user,
    get_blocks_for_tasks,
    get_required_tariffs,
    get_states,
    get_tariffs,
    is_block_accessible,
    portfolio_window_expired,
    start_portfolio_window,
)
from app.services.tracker import (
    accessible_cycles,
    accessible_task_entries,
    cycle_bounds,
    cycle_label,
    effective_cycle,
    effective_week_start,
)

STATUS_LOCKED = "locked"
STATUS_CURRENT = "current"

# Почему шаг заперт: очередью, открытием по календарю или закрытием по
# календарю. Ученику это разные сообщения — «сделай предыдущее» против
# «откроется 23 сентября» против «доступ закрыт» (владелец 03.09.2026,
# LOCK_BY_CLOSED добавлен 10.09.2026). Отдельная причина для закрытия, а не
# общая с открытием: у закрытого навсегда блока `opens_on` посчитать не из
# чего, а «Откроется …» для него — неправда.
LOCK_BY_SEQUENCE = "sequence"
LOCK_BY_DATE = "date"
LOCK_BY_CLOSED = "closed"


def _not_open_yet(value: datetime | None, now: datetime) -> bool:
    """Момент открытия ещё не наступил. `None` — открыто всегда."""
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value > now


def _already_closed(value: datetime | None, now: datetime) -> bool:
    """Момент закрытия уже прошёл. `None` — не закрывается никогда."""
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= now


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


def current_feed_task_ids(db: Session, *, user_id: int, today: date) -> set[int]:
    """IDs заданий, которые сейчас есть в основной ленте ученика.

    Это read-only версия первого шага ``feed_for_student``. Трекеру нужен
    только ответ, можно ли вести ученика кнопкой «Перейти» в текущую ленту;
    строить ради этого все блоки нельзя, потому что сборка ленты запускает
    персональные окна портфолио при первом показе.
    """
    topic, start, end = feed_window(db, user_id, today)
    window_start, _ = day_bounds(start)
    _, window_end = day_bounds(end)
    entries = accessible_task_entries(
        db,
        user_id,
        start=window_start,
        end=window_end,
        topic_id=topic.id if topic is not None else None,
        include_undated=topic is not None,
    )
    return {entry["task"].id for entry in entries}


def _task_done(entry: dict) -> bool:
    return entry["status"] == STATUS_DONE


def has_portfolio_upload(db: Session, user_id: int, *, since: date | datetime) -> bool:
    """Загружал ли ученик работу «До» начиная с `since` (московская дата).

    Блок «Загрузить портфолио» закрывается фактом загрузки, а не галочкой
    (владелец 03.09.2026: «пока не будет подтверждения, что он загрузил
    портфолио, которое именно 18 числа, у него не откроется актуальное
    образовательное пространство дальше»). Отсчёт — от начала цикла: прошлогодняя
    работа не должна засчитывать сегодняшнее задание.

    Тип работы и статус проверяются вместе с датой (владелец 09.09.2026: «по
    этой кнопке работы загружаются в ДО»). Условие обязано совпадать с
    `api/cabinet_tracker.py::_portfolio_block_done`: разойдутся — блок будет
    рисоваться закрытым и при этом запирать ленту, или наоборот.
    """
    start = (
        day_bounds(since)[0]
        if isinstance(since, date) and not isinstance(since, datetime)
        else since
    )
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return (
        db.query(Work.id)
        .filter(
            Work.user_id == user_id,
            Work.work_type == WORK_TYPE_BEFORE,
            Work.status == "success",
            Work.created_at >= start,
        )
        .first()
        is not None
    )


def build_cycle_feed(
    db: Session, *, user_id: int, user_tariff: str | None, start: date, end: date,
    topic_id: int | None = None,
) -> list[dict]:
    """Шаги ленты за период `[start, end]`, сверху вниз, со статусом ученика.

    Шаг — словарь `{"block", "task", "status", "state", "entry",
    "first_in_task"}`. `block` — `None` у задачи без блоков: она идёт в ленте
    одной карточкой и закрывается так же, как закрывалась во вкладках.

    `first_in_task` — шаг первого видимого блока задания. Экран печатает имя
    задания только над ним (владелец 16.09.2026): у блоков без своего
    заголовка карточка подставляла имя задания, и два видео подряд выходили
    подписаны одинаково.

    Статус: `"done"` — закрыт, `"current"` — можно делать сейчас, `"locked"` —
    ждёт того, что выше. Блокировка считается сквозной: список блоков всех
    задач склеивается в один и отдаётся `is_block_accessible`, который уже
    умеет три условия разом (период доступа, тариф, последовательность).

    Билет Пробника (`ITEM_MOCK_EXAM`) в ленте показывается, но хвост не
    запирает — он блокирует месяц, а не цикл (решение владельца 23.08,
    подтверждено 24.08). Ради этого его собственная обязательность в
    последовательности игнорируется.

    `topic_id` (10.09.2026) — id настоящего цикла (`LearningTopic(kind='week')`),
    если он есть (`feed_window` вернул не запасную календарную неделю). Только
    тогда в выборку подмешиваются задания без даты (`include_undated=True`,
    см. `accessible_task_entries`) — у запасной календарной недели цикла нет,
    и бездатным заданиям там взяться неоткуда.
    """
    window_start, _ = day_bounds(start)
    _, window_end = day_bounds(end)
    entries = accessible_task_entries(
        db, user_id, start=window_start, end=window_end,
        topic_id=topic_id, include_undated=topic_id is not None,
    )
    if not entries:
        return []

    now = datetime.now(timezone.utc)

    tasks = [entry["task"] for entry in entries]
    blocks_by_task = get_blocks_for_tasks(db, [task.id for task in tasks])

    # Вопрос «покажите только после закрытия задания» до этого момента в ленте
    # не участвует вообще (найдено 07.09.2026). Панель задания его прятала
    # (`visible_question_blocks`), а лента показывала как обычный шаг — и,
    # если он был отмечен обязательным, запирала им весь хвост: ответить
    # нельзя, потому что не видно, а не ответишь — дальше не пустят. Тот же
    # тупик, который 31.08.2026 уже развязывали на уровне закрытия задания.
    for entry in entries:
        task_id = entry["task"].id
        if _task_done(entry):
            continue
        blocks_by_task[task_id] = [
            block for block in blocks_by_task.get(task_id, [])
            if not block.hidden_until_done
        ]

    # Сквозной список блоков в порядке ленты — на нём и считается блокировка.
    ordered_blocks = []
    required_by_block: dict[int, bool] = {}
    for entry in entries:
        task = entry["task"]
        task_blocks = blocks_by_task.get(task.id, [])
        ordered_blocks.extend(task_blocks)
        # Флаг задания стоит над флагами его блоков. Если преподаватель снял
        # обязательность у задания целиком, ни один дочерний блок не должен
        # запирать хвост ленты или следующий цикл. Пробник сохраняет прежнее
        # исключение: он блокирует месяц, а не учебную ленту.
        task_blocks_progress = task.is_required and task.kind != ITEM_MOCK_EXAM
        for block in task_blocks:
            required_by_block[block.id] = bool(
                task_blocks_progress and block.is_required
            )
    block_ids = [block.id for block in ordered_blocks]
    states = get_states(db, block_ids=block_ids, user_id=user_id)
    tariffs_by_block = get_tariffs(db, block_ids)
    required_tariffs_by_block = get_required_tariffs(db, block_ids)

    # Блок «Загрузить портфолио» закрывается фактом загрузки работы, а не
    # галочкой ученика (владелец 03.09.2026). Закрываем по-настоящему, а не
    # только в отрисовке: иначе закрытый на экране блок продолжал бы запирать
    # всё, что ниже, — последовательность считается по состояниям блоков. Тот
    # же приём, что у видео (`api/video.py::_close_video_task_once`).
    # Блоки «Домашнее задание» и «Работа на время» сюда не входят: с
    # 07.09.2026 они принимают файлы сами и закрываются в момент загрузки
    # (`api/cabinet_tracker.py::upload_task_block_work`). Пересчёт по портфолио
    # закрывал бы их любой посторонней работой, загруженной на общем экране.
    pending_uploads = [
        block for block in ordered_blocks
        if block.block_type == BLOCK_PORTFOLIO
        and (block.id not in states or states[block.id].status != STATUS_DONE)
    ]
    closed_upload = False
    for block in pending_uploads:
        state = states.get(block.id)
        # Для персонального окна работа, загруженная до его старта, не может
        # закрыть новый шаг. Первый показ ниже создаст started_at; со
        # следующего запроса считаем только более свежие загрузки.
        if (
            block.portfolio_window_hours
            and (state is None or state.started_at is None)
        ):
            continue
        upload_since = (
            state.started_at
            if block.portfolio_window_hours and state is not None and state.started_at
            else start
        )
        if has_portfolio_upload(db, user_id, since=upload_since):
            close_block_for_user(
                db, block=block, user_id=user_id, source="portfolio_upload"
            )
            closed_upload = True
    if closed_upload:
        db.commit()
        states = get_states(db, block_ids=block_ids, user_id=user_id)

    # Задача без блоков участвует в блокировке хвоста наравне с блоками: пока
    # обязательное видео не досмотрено, следующее задание ленты закрыто.
    # `blocked` взводится один раз и дальше запирает всё, что ниже.
    blocked = False
    steps: list[dict] = []
    block_index = 0
    started_portfolio_window = False
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
                # Задание без блоков — одна карточка, и она же первая: иначе
                # экран «Материалы задания» остался бы вообще без подписи.
                "first_in_task": True,
                "last_in_task": True,
            })
            if (
                not done
                and task.is_required
                and task.kind != ITEM_MOCK_EXAM
            ):
                blocked = True
            continue

        for position, block in enumerate(task_blocks):
            state = states.get(block.id)
            done = state is not None and state.status == STATUS_DONE
            block_waits_date = _not_open_yet(block.opens_at, now)
            block_closed = (
                portfolio_window_expired(block, state, now=now)
                if block.block_type == BLOCK_PORTFOLIO and block.portfolio_window_hours
                else _already_closed(block.closes_at, now)
            )
            accessible = (
                not blocked
                and not task_waits_date
                and is_block_accessible(
                    block_index=block_index,
                    blocks=ordered_blocks,
                    states=states,
                    tariffs_by_block=tariffs_by_block,
                    user_tariff=user_tariff,
                    required_tariffs_by_block=required_tariffs_by_block,
                    required_by_block=required_by_block,
                    now=now,
                )
            )
            if (
                accessible
                and block.block_type == BLOCK_PORTFOLIO
                and block.portfolio_window_hours
            ):
                was_started = state is not None and state.started_at is not None
                state = start_portfolio_window(
                    db, block=block, user_id=user_id, now=now
                )
                states[block.id] = state
                started_portfolio_window = (
                    started_portfolio_window or not was_started
                )
            if done:
                status, lock_reason = STATUS_DONE, None
            elif accessible:
                status, lock_reason = STATUS_CURRENT, None
            elif block_closed:
                status, lock_reason = STATUS_LOCKED, LOCK_BY_CLOSED
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
                # Первый **видимый** блок: `task_blocks` выше уже очищен от
                # блоков `hidden_until_done` незакрытого задания.
                "first_in_task": position == 0,
                "last_in_task": position == len(task_blocks) - 1,
            })
            block_index += 1
    if started_portfolio_window:
        db.commit()
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
        db, user_id=user_id, user_tariff=user_tariff, start=start, end=end,
        topic_id=topic.id if topic is not None else None,
    )
    cycles = started_cycles(db, user_id, today)
    # «Следующее задание откроется 23 сентября» (владелец 03.09.2026): подсказка
    # тому, кто закрыл всё доступное и упёрся в календарь, а не в собственные
    # долги. Если впереди есть хоть один шаг, который можно делать сейчас,
    # подсказки нет — она бы только отвлекала.
    waiting_for = None
    if steps and not any(step["status"] == STATUS_CURRENT for step in steps):
        upcoming = [
            step["opens_on"] for step in steps
            if step["status"] == STATUS_LOCKED
            and step["lock_reason"] == LOCK_BY_DATE
            and step["opens_on"] is not None
        ]
        if upcoming:
            waiting_for = min(upcoming)
    return {
        "topic": topic,
        "start": start,
        "end": end,
        "steps": steps,
        # Список пройденных циклов для возврата; текущий помечен отдельно.
        "cycles": [
            {
                "id": item.id,
                "title": cycle_label(item),
                "start": cycle_bounds(item)[0],
                "end": cycle_bounds(item)[1],
                "is_current": topic is not None and item.id == topic.id,
            }
            for item in cycles
        ],
        "waiting_for": waiting_for,
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
