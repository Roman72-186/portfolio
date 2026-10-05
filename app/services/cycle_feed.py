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

from app.models.learning_topic import (
    TOPIC_KIND_PERIOD,
    TOPIC_KIND_STAGE,
    TOPIC_KIND_WEEK,
    LearningTopic,
)
from app.models.task_block import BLOCK_PORTFOLIO, COMPLETABLE_BLOCK_TYPES
from app.models.tracker import ITEM_MOCK_EXAM, STATUS_DONE, TrackerTask
from app.models.user import User
from app.models.work import WORK_TYPE_BEFORE, Work
from app.services.program import day_bounds
from app.services.task_blocks import (
    close_block_for_user,
    get_blocks_for_tasks,
    get_required_tariffs,
    get_submit_deadlines,
    get_task_submit_deadlines,
    get_states,
    get_tariffs,
    is_block_accessible,
    is_block_open_for_tariff,
    required_by_block_for_task,
    poll_inner_block_ids,
    portfolio_window_expired,
    start_portfolio_window,
)
from app.services.tracker import (
    accessible_cycles,
    accessible_task_entries,
    cycle_bounds,
    cycle_debt,
    cycle_done_by_user,
    cycle_label,
    effective_cycle,
    effective_week_start,
    locked_cycle_ids,
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

    Включает и задания этапа-родителя («Портфолио»): в ленте цикла их нет, но
    они в одном тапе от неё — кнопкой этапа в карусели, и ссылка трекера
    открывает ленту этапа сама (`api/cabinet_learning.py`, параметр `task`).
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
    task_ids = {entry["task"].id for entry in entries}
    stage = _stage_of(db, topic)
    if stage is not None:
        task_ids |= {
            entry["task"].id for entry in stage_task_entries(db, user_id, stage, today)
        }
    return task_ids


def _stage_of(db: Session, topic: LearningTopic | None) -> LearningTopic | None:
    """Этап цикла (или сам этап, если передан этап). Удалённый — как нет."""
    if topic is None:
        return None
    if topic.kind == TOPIC_KIND_STAGE:
        return topic
    if topic.parent_id is None:
        return None
    stage = db.get(LearningTopic, topic.parent_id)
    if stage is None or stage.deleted_at is not None or stage.kind != TOPIC_KIND_STAGE:
        return None
    return stage


def stage_task_entries(
    db: Session, user_id: int, stage: LearningTopic, today: date
) -> list[dict]:
    """Доступные ученику задания, заведённые прямо на этапе («Портфолио»).

    Только чтение: ленту этапа не строит и окон портфолио не запускает — окно
    стартует, когда ученик сам открыл этап. Дофильтр по `topic_id` нужен
    потому, что `accessible_task_entries(topic_id=...)` сужает лишь бездатную
    ветку, а датные задания циклов этапа попали бы в широкое окно этапа по
    совпадению дат.

    Закончившийся этап заданий не отдаёт (владелец 05.10.2026: «нужно скрыть,
    если что добавим вручную»). Должник «Предобучения» стоит в его цикле и
    после 04.10, и «Портфолио» прошлого этапа висело кнопкой рядом с циклами
    «1 семестра». Его циклы должнику по-прежнему открыты, кнопка — нет.
    """
    if _cycle_is_over(stage, today):
        return []
    first, last = cycle_bounds(stage)
    window_start, _ = day_bounds(first)
    _, window_end = day_bounds(last)
    return [
        entry for entry in accessible_task_entries(
            db, user_id, start=window_start, end=window_end,
            topic_id=stage.id, include_undated=True,
        )
        if entry["task"].topic_id == stage.id
    ]


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

    Задания, заведённые прямо на этапе («Портфолио»), в ленту цикла не
    попадают: у них своя лента — этап, открытый кнопкой в карусели
    (`feed_for_student(cycle_id=<id этапа>)`). С 24.09 по 29.09.2026 они
    приклеивались первыми к ленте каждого цикла, и владелец 29.09.2026 попросил
    это убрать: «оно во всех циклах почему-то, а должно быть отдельно, как
    цикл».
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
    student = db.get(User, user_id)
    is_intake_student = bool(student and student.access_until is not None)

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

    # Блок, закрытый чужим тарифом, из ленты убирается совсем — его для этого
    # ученика не существует (владелец 06.09.2026: «не серым „недоступно на
    # вашем тарифе“, а не показывать вообще»). Решение было записано в
    # докстринге `is_block_accessible`, но до шаблона ленты его не довели, и до
    # 28.09.2026 ученик чужого тарифа читал название чужого урока с подписью
    # «Откроется, когда будет сделано предыдущее» — открыться оно не могло
    # никогда. Правило одно на все слои — `visible_blocks_for_student`; здесь
    # взят его предикат, потому что тарифы всё равно нужны ниже целым словарём
    # и второй запрос за тем же был бы лишним.
    tariffs_by_block = get_tariffs(
        db,
        [block.id for task_blocks in blocks_by_task.values() for block in task_blocks],
    )
    for entry in entries:
        task_id = entry["task"].id
        blocks_by_task[task_id] = [
            block
            for block in blocks_by_task.get(task_id, [])
            if is_block_open_for_tariff(tariffs_by_block.get(block.id), user_tariff)
        ]

    # Сквозной список блоков в порядке ленты — на нём и считается блокировка.
    ordered_blocks = []
    required_by_block: dict[int, bool] = {}
    for entry in entries:
        task = entry["task"]
        task_blocks = blocks_by_task.get(task.id, [])
        ordered_blocks.extend(task_blocks)
        # Флаг задания над флагами блоков, опрос, диагностика — одно правило
        # с `feed_state` и кнопкой «Завершить задание».
        required_by_block.update(required_by_block_for_task(
            task, task_blocks, is_intake_student=is_intake_student
        ))
    block_ids = [block.id for block in ordered_blocks]
    states = get_states(db, block_ids=block_ids, user_id=user_id)
    required_tariffs_by_block = get_required_tariffs(db, block_ids)
    submit_deadlines_by_block = get_submit_deadlines(db, block_ids)
    # Сроки на уровне задания — запасные для блоков, которые своего не задали
    # (владелец 27.09.2026). Ключ — задание, потому что в ленте цикла блоки
    # идут подряд из разных заданий.
    tasks_by_id = {entry["task"].id: entry["task"] for entry in entries}
    task_deadlines_by_task = get_task_submit_deadlines(db, list(tasks_by_id))

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
        # Момент, а не дата: с 27.09.2026 открытие несёт время суток, и
        # «Откроется 23.09.2026» у задания, открывающегося в 10:00, было бы
        # полуправдой. Печатает фильтр `msk_text` (`app/tmpl.py`).
        opens_on = task.starts_at if task_waits_date else None

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
            # Текст и ссылку отметить нечем (`COMPLETABLE_BLOCK_TYPES`), поэтому
            # они «сделаны», когда закрыто их задание — кнопкой или само
            # (владелец 01.10.2026, аудит АОП ученика, находка 3). Иначе
            # закрытое задание из текста и ссылки показывало «Сделано 0 из 2»,
            # а его шаги не сворачивались. На очередь ленты это не влияет:
            # `is_block_accessible` смотрит состояния блоков в базе.
            done = (state is not None and state.status == STATUS_DONE) or (
                block.block_type not in COMPLETABLE_BLOCK_TYPES and _task_done(entry)
            )
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
                    submit_deadlines_by_block=submit_deadlines_by_block,
                    tasks_by_id=tasks_by_id,
                    task_submit_deadlines_by_task=task_deadlines_by_task,
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
                    block.opens_at if block_waits_date else opens_on
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
    _mark_sequence_holders(steps, required_by_block)
    return steps


def _mark_sequence_holders(steps: list[dict], required_by_block: dict[int, bool]) -> None:
    """Запертому очередью шагу — какой шаг его держит (`step["blocked_by"]`).

    Очередь одна на весь цикл и предмета не знает (владелец 01.10.2026:
    вкладки «Общее / Композиция / Рисунок» очередь не делят). Держащий шаг
    может стоять в другой вкладке, и без подписи ученик видел бы «Откроется,
    когда будет сделано предыдущее», не находя этого предыдущего на экране.
    Держит первый невыполненный обязательный шаг выше — до него ученик и
    должен дойти первым. Это подпись, а не правило: само запирание решает
    `is_block_accessible` и флаг `blocked` выше. Держащий шаг того же
    задания шаблон по имени не называет — он стоит прямо выше.
    """
    holder = None
    for step in steps:
        if (
            holder is not None
            and step["status"] == STATUS_LOCKED
            and step["lock_reason"] == LOCK_BY_SEQUENCE
        ):
            step["blocked_by"] = holder
        if holder is not None or step["status"] == STATUS_DONE:
            continue
        block = step["block"]
        task = step["task"]
        required = (
            required_by_block.get(block.id, False)
            if block is not None
            else task.is_required and task.kind != ITEM_MOCK_EXAM
        )
        if required:
            holder = {
                "title": (block.title if block is not None and block.title else task.title),
                "subject": step["subject"] or "",
                "task_id": task.id,
            }


def started_cycles(
    db: Session, user_id: int, today: date, *, stage_id: int | None = None
) -> list[LearningTopic]:
    """Циклы ученика, которые уже начались, от поздних к ранним.

    Нужны для возврата в пройденное: «он может вернуться в этот цикл, зайти в
    этот цикл, потому что у каждого цикла своя тема в обучении» (владелец
    03.09.2026). Не начавшиеся не показываем — программа вперёд не выдаётся.

    `stage_id` (владелец 24.09.2026, Этапы) сужает список до циклов
    конкретного этапа — так задан архив в карусели: «пока этап не закрылся,
    ученик видит прошлые циклы этого этапа», не всех этапов сразу. `None` —
    старое поведение без сужения: единственный случай, когда он проставляется
    явно, — легаси-цикл без `parent_id` (заведён до 24.09.2026 либо этапу не
    назначен), тогда все такие бесхозные циклы по-прежнему показываются одним
    общим архивом, как было до Этапов.
    """
    started = [
        topic for topic in accessible_cycles(db, user_id)
        if cycle_bounds(topic)[0] <= today
        and (stage_id is None or topic.parent_id == stage_id)
    ]
    return list(reversed(started))


def _carousel_cycles(
    db: Session, user_id: int, today: date, stage_id: int | None
) -> list[LearningTopic]:
    """Циклы для карусели: текущего этапа и этапов, чья крайняя дата ещё не
    прошла, от поздних к ранним.

    Владелец 04.10.2026: последний день «Предобучения» совпал с первым днём
    семестра, ученица закрыла всё, встала на цикл «Октябрь» нового этапа — и
    кнопка «Итоговая встреча» в «Занятии 4 октября» пропала вместе со всем
    прежним этапом. Этап показывается, «пока крайняя дата не прошла».
    """
    if stage_id is None:
        return started_cycles(db, user_id, today)
    open_stage_ids = {stage_id}
    result = []
    for topic in started_cycles(db, user_id, today):
        parent_id = topic.parent_id
        if parent_id is None:
            continue
        if parent_id not in open_stage_ids:
            stage = db.get(LearningTopic, parent_id)
            if stage is None or cycle_bounds(stage)[1] < today:
                continue
            open_stage_ids.add(parent_id)
        result.append(topic)
    return result


def cycle_is_archived_for_user(
    db: Session, user_id: int, topic_id: int, today: date
) -> bool:
    """Цикл `topic_id` для ученика — архив (только просмотр).

    Архив — закончившийся цикл (`kind='week'`), который не совпадает с тем,
    на котором ученик стоит сейчас (`effective_cycle`). Владелец 24.09.2026:
    пока этап открыт, прошлые циклы доступны только на чтение. Прямая ссылка
    на цикл закрытого этапа тоже остаётся архивом (не 404) — тот же принцип,
    что у прошедшей темы вообще («учебный архив», `models/learning_topic.py`).
    """
    topic = db.get(LearningTopic, topic_id)
    if topic is None or topic.kind != TOPIC_KIND_WEEK:
        return False
    if not _cycle_is_over(topic, today):
        return False
    current = effective_cycle(db, user_id, today)
    return current is None or current.id != topic.id


def task_is_archived_for_user(
    db: Session, user_id: int, task: TrackerTask, today: date
) -> bool:
    """Задание для ученика — архив (только просмотр). Этим спрашивают пишущие
    роуты, а не `cycle_is_archived_for_user` по `task.topic_id` напрямую.

    Правило — то же, что у экрана: что ученик видит в неархивном цикле, то он
    и может делать. Лента любого цикла (текущего или открытого через
    карусель, `feed_for_student(cycle_id=...)`) берёт датные задания по датам
    из всех доступных циклов (`accessible_task_entries`), поэтому задание,
    приписанное к закончившемуся циклу, но стоящее датой в идущем, ученик
    видит там и может отмечать. Прецедент 28.09.2026: видео в цикле 3 не
    отмечалось — гейт смотрел только на `topic_id` и отвечал 403 «Цикл
    пройден», экран показывал «Не удалось отметить». Первая починка сверялась
    только с лентой текущего цикла и промахнулась мимо ученика с долгом:
    текущим у него был ранний цикл, а цикл 3 он открыл через карусель.

    Этап («Портфолио») здесь не считается: его лента охватывает весь этап, и
    через неё открылись бы на запись задания всех прошлых циклов.
    """
    if task.topic_id is None:
        return False
    if not cycle_is_archived_for_user(db, user_id, task.topic_id, today):
        return False
    if task.id in current_feed_task_ids(db, user_id=user_id, today=today):
        return False
    if task.due_at is None:
        return True
    due_at = task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=timezone.utc)
    for cycle in started_cycles(db, user_id, today):
        if cycle.kind != TOPIC_KIND_WEEK or cycle.id == task.topic_id:
            continue
        first, last = cycle_bounds(cycle)
        if not day_bounds(first)[0] <= due_at < day_bounds(last)[1]:
            continue
        if not cycle_is_archived_for_user(db, user_id, cycle.id, today):
            return False
    return True


def _debt_view(db: Session, debt: dict, viewed: LearningTopic | None) -> dict:
    """Плашка долга над лентой: какой цикл закрыть, что в нём осталось и что
    откроется следом. Запертый цикл другого этапа подписан этапом — иначе
    «Цикл 1» следующего этапа не отличить от «Цикла 1» текущего."""
    cycle = debt["cycle"]
    following = debt["locked"][0]
    following_label = cycle_label(db, following)
    if following.parent_id is not None and following.parent_id != cycle.parent_id:
        stage = db.get(LearningTopic, following.parent_id)
        if stage is not None and stage.title:
            following_label = f"{stage.title}: {following_label}"
    return {
        "cycle_id": cycle.id,
        "label": cycle_label(db, cycle),
        "tasks": [task.title for task in debt["tasks"]],
        "next": following_label,
        "is_viewed": viewed is not None and viewed.id == cycle.id,
    }


def task_is_locked_for_user(
    db: Session, user_id: int, task: TrackerTask, today: date
) -> bool:
    """Задание запертого долгом цикла (владелец 30.09.2026: «не пускать
    вперёд»). Этим спрашивают роуты рядом с `task_is_archived_for_user`:
    пока не закрыт долг, в следующем цикле нельзя ни открыть задание, ни
    отметить шаг, ни сдать работу."""
    if task.topic_id is None:
        return False
    return task.topic_id in locked_cycle_ids(db, user_id, today)


def _cycle_is_over(topic: LearningTopic, today: date) -> bool:
    """Цикл закончился — дата окончания уже прошла.

    Архивом (только просмотр) бывает только закончившийся цикл. Идущий цикл
    не архив, даже если «текущим» для ученика выбран другой: даты циклов в
    этапе могут пересекаться. Прецедент 25.09.2026: «Цикл 1» (23–27.09) и
    «Цикл 2» (16.09–04.10) шли одновременно, текущим стал второй, и все
    отметки в первом сервер отклонял 403 «Цикл пройден», хотя цикл ещё шёл.
    """
    return cycle_bounds(topic)[1] < today


def _archive_levels(
    db: Session, cycle: LearningTopic
) -> tuple[LearningTopic | None, LearningTopic | None]:
    """Период и этап цикла для архива (владелец 05.10.2026: «Период →
    Этап → Цикл → задания»).

    С 06.10.2026 период — отдельная запись `kind='period'` над этапом
    (`stage.parent_id`), и каждый этап внутри периода. Владелец 06.10.2026
    («да, показывать»): в архиве настоящий период. До этого уровня в базе не
    было, и этап повторял период тем же названием — так и остаётся, если
    периода у этапа нет (данные до 06.10.2026), он удалён или скрыт галочкой
    «Показывать ученикам»: скрытый период ученик не видит и здесь. Цикл без
    этапа (до 24.09.2026) — `(None, None)`.
    """
    stage = _stage_of(db, cycle)
    if stage is None:
        return None, None
    period = db.get(LearningTopic, stage.parent_id) if stage.parent_id is not None else None
    if (
        period is None
        or period.kind != TOPIC_KIND_PERIOD
        or period.deleted_at is not None
        or not period.is_published
    ):
        return stage, stage
    return period, stage


def archive_for_student(
    db: Session, *, user_id: int, user_tariff: str | None, today: date
) -> list[dict]:
    """Архив ученика — пройденные циклы, сгруппированные период → этап →
    цикл, от ранних к поздним (владелец 05.10.2026).

    История: 04.10.2026 архив собирал только видео (ученица не нашла прошлые
    ролики — полоса циклов на экране обучения показывает лишь текущий этап).
    05.10.2026 служба заботы: «должна была быть полная архивация периода со
    всеми заданиями, видео, голосовыми, работами ребенка». Владелец: в архиве
    список периодов, внутри этапы, внутри циклы; цикл открывается тут же, в
    архиве, а не в ленте обучения (`/cabinet/learning/archive/{id}`).

    Пройденный цикл — закончился или ученик его выполнил
    (`cycle_done_by_user`; владелец 04.10.2026: «Цикл 4» закрыли 83 ученика
    из 98 до его конца). Цикл без обязательных заданий выполненным не
    считается, иначе он уезжал бы в архив в первый же день. Своих правил
    видимости нет: циклы из `started_cycles` (аудитория, «закончился до
    прихода»), шаги из той же `build_cycle_feed`, что и лента; цикл без
    единого шага не показывается. Цикл, запертый долгом, пропускается:
    вперёд нельзя (30.09.2026).
    """
    debt = cycle_debt(db, user_id, today)
    locked_ids = {item.id for item in debt["locked"]} if debt else set()
    periods: dict[int | None, dict] = {}

    for cycle in reversed(started_cycles(db, user_id, today)):
        if cycle.id in locked_ids:
            continue
        first, last = cycle_bounds(cycle)
        if not (last < today or cycle_done_by_user(db, user_id, cycle)):
            continue
        steps = build_cycle_feed(
            db, user_id=user_id, user_tariff=user_tariff, start=first, end=last,
            topic_id=cycle.id,
        )
        if not steps:
            continue
        period_topic, stage_topic = _archive_levels(db, cycle)
        period_key = period_topic.id if period_topic is not None else None
        if period_key not in periods:
            periods[period_key] = {
                "topic": period_topic,
                "title": period_topic.title if period_topic is not None else "Ранние циклы",
                "stages": [],
            }
        stages = periods[period_key]["stages"]
        stage_key = stage_topic.id if stage_topic is not None else None
        # Этап ищется среди всех этапов периода, а не только последнего: этапы
        # одного периода идут в одни даты («Октябрь» и «1 семестр_годовой курс»
        # оба с 04.10.2026), и при чередовании циклов этап встал бы дважды.
        stage_group = next((group for group in stages if group["key"] == stage_key), None)
        if stage_group is None:
            stage_group = {
                "key": stage_key,
                "title": stage_topic.title if stage_topic is not None else "",
                "cycles": [],
            }
            stages.append(stage_group)
        stage_group["cycles"].append({
            "id": cycle.id, "title": cycle_label(db, cycle), "start": first, "end": last,
        })

    # Циклы без этапа старше любых этапов — они идут первыми.
    return sorted(
        periods.values(),
        key=lambda period: (
            period["topic"] is not None,
            cycle_bounds(period["topic"])[0] if period["topic"] is not None else date.min,
        ),
    )


def archive_cycle_ids(
    db: Session, *, user_id: int, user_tariff: str | None, today: date
) -> set[int]:
    """Циклы, которые архив показывает ученику, — им и только им разрешён
    экран цикла в архиве. Одна функция с самим списком, иначе ссылка и
    экран разойдутся."""
    return {
        cycle["id"]
        for period in archive_for_student(
            db, user_id=user_id, user_tariff=user_tariff, today=today,
        )
        for stage in period["stages"]
        for cycle in stage["cycles"]
    }


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

    `cycle_id` также принимает id самого этапа — так открывается «Портфолио»
    (владелец 24.09.2026: «просто открываем все задания, которые есть», не
    прыжок к одному блоку внутри текущего цикла). Разрешён только этап
    текущего цикла ученика — иначе подобранный в адресной строке id открыл
    бы чужой этап.
    """
    current_topic, current_start, current_end = feed_window(db, user_id, today)
    # Вперёд нельзя (владелец 30.09.2026): цикл после долга не открывается даже
    # по прямой ссылке — показываем текущий, а долг и что он запирает — плашкой.
    debt = cycle_debt(db, user_id, today)
    locked_ids = {item.id for item in debt["locked"]} if debt else set()
    chosen = None
    if cycle_id is not None and cycle_id not in locked_ids:
        chosen = next(
            (t for t in started_cycles(db, user_id, today) if t.id == cycle_id), None
        )
    chosen_stage = None
    if chosen is None and cycle_id is not None:
        candidate_stage_id = current_topic.parent_id if current_topic is not None else None
        if candidate_stage_id == cycle_id:
            stage_candidate = db.get(LearningTopic, cycle_id)
            # Закончившийся этап по прямой ссылке не открывается: его кнопка
            # уже снята (`stage_task_entries`), старая ссылка вела бы туда же.
            if (
                stage_candidate is not None
                and stage_candidate.kind == TOPIC_KIND_STAGE
                and stage_candidate.deleted_at is None
                and not _cycle_is_over(stage_candidate, today)
            ):
                chosen_stage = stage_candidate
    if chosen is not None:
        start, end = cycle_bounds(chosen)
        topic = chosen
    elif chosen_stage is not None:
        start, end = cycle_bounds(chosen_stage)
        topic = chosen_stage
    else:
        topic, start, end = current_topic, current_start, current_end
    steps = build_cycle_feed(
        db, user_id=user_id, user_tariff=user_tariff, start=start, end=end,
        topic_id=topic.id if topic is not None else None,
    )
    viewing_stage_directly = topic is not None and topic.kind == TOPIC_KIND_STAGE
    if viewing_stage_directly:
        # `accessible_task_entries(topic_id=...)` сужает только бездатную
        # ветку (см. её докстринг) — датная задача чужого цикла того же
        # этапа могла попасть в окно просто по совпадению дат (окно этапа
        # широкое, покрывает все его циклы разом). У «Портфолио» на этапе
        # своих чужих задач не бывает — дофильтровать явно.
        steps = [step for step in steps if step["task"].topic_id == topic.id]
    # Кнопки перед циклами в карусели — задания, заведённые прямо на этапе
    # отображаемого цикла. В ленту цикла они не входят (владелец 29.09.2026:
    # «должно быть отдельно, как цикл»), кнопка открывает сам этап.
    stage_of_topic = _stage_of(db, topic)
    pinned_tasks = [
        {
            "id": entry["task"].id,
            "title": entry["task"].title,
            "is_current": viewing_stage_directly,
        }
        for entry in (
            stage_task_entries(db, user_id, stage_of_topic, today)
            if stage_of_topic is not None else []
        )
    ]
    # Карусель показывает циклы текущего этапа и этапов, чья крайняя дата ещё
    # не прошла (`_carousel_cycles`, 04.10.2026) — прямая ссылка на старый
    # цикл (`chosen` выше) при этом ищется без сужения по этапу, владелец
    # 24.09.2026 просил её не запирать.
    stage_id = current_topic.parent_id if current_topic is not None else None
    stage = None
    if stage_id is not None:
        stage_topic = db.get(LearningTopic, stage_id)
        if stage_topic is not None:
            stage = {"id": stage_topic.id, "label": stage_topic.title or ""}
    cycles = _carousel_cycles(db, user_id, today, stage_id)
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
    poll_inner = poll_inner_block_ids([step["block"] for step in steps if step["block"] is not None])
    counted_steps = [
        step for step in steps
        if step["block"] is None or step["block"].id not in poll_inner
    ]
    return {
        "topic": topic,
        "start": start,
        "end": end,
        "steps": steps,
        # Список пройденных циклов для возврата; текущий помечен отдельно.
        "cycles": [
            {
                "id": item.id,
                "title": cycle_label(db, item),
                "start": cycle_bounds(item)[0],
                "end": cycle_bounds(item)[1],
                "is_current": topic is not None and item.id == topic.id,
                "is_locked": item.id in locked_ids,
            }
            for item in cycles
        ],
        "debt": _debt_view(db, debt, topic) if debt else None,
        "stage": stage,
        # Кнопки перед циклами в карусели («Портфолио») — задания, заведённые
        # прямо на этапе. Якорь на них уже есть у любого шага ленты
        # (`id="learning-task-{{ task.id }}"` у первого блока задания).
        "pinned_tasks": pinned_tasks,
        "waiting_for": waiting_for,
        # Открыт прошлый цикл, а не тот, на котором ученик стоит сейчас:
        # экран показывает его только для чтения.
        # Только закончившийся цикл: то же правило, что у пишущих эндпоинтов
        # (`cycle_is_archived_for_user`), иначе экран спрятал бы кнопки там,
        # где сервер отметку примет.
        "is_archive": (
            chosen is not None
            and (current_topic is None or chosen.id != current_topic.id)
            and _cycle_is_over(chosen, today)
        ),
        # Опрос ученик видит одной карточкой (владелец 30.09.2026) — и
        # считается он одним шагом, по последнему вопросу: иначе опрос из
        # трёх вопросов давал бы «Сделано 3 из 4» при двух карточках.
        "done_count": sum(1 for step in counted_steps if step["status"] == STATUS_DONE),
        "total_count": len(counted_steps),
        # Вкладки «Общее / Композиция / Рисунок» (созвон 30.09.2026, владелец
        # 01.10.2026): если хоть у одного шага есть предмет — все три, иначе
        # переключателя нет (владелец 03.09.2026: «мы просто не будем ставить
        # разделение, и кнопок в принципе не будет»). «Общее» — шаги без
        # предмета, значение пустое.
        "subject_tabs": _subject_tabs(steps),
        # Открытая по умолчанию вкладка — та, где первый невыполненный шаг:
        # туда ученику и идти. Всё сделано — «Общее».
        "default_subject": next(
            (step["subject"] or "" for step in steps if step["status"] != STATUS_DONE), ""
        ),
    }


# Отказ перехода по кнопке-ссылке: `main.py::forbidden_handler` рисует его
# заглушкой со своим заголовком, а не «Аккаунт заблокирован».
LINK_LOCKED_DETAIL = (
    "Ссылка откроется, когда будут сделаны все задания перед ней. "
    "Вернись в «Обучение» и посмотри, что осталось."
)


def block_step_is_open(
    db: Session, *, user_id: int, user_tariff: str | None, task: TrackerTask,
    block_id: int, today: date,
) -> bool:
    """Открыт ли ученику шаг-блок `block_id`: в ленте он «можно делать» или «сделано».

    Нужна переходу по кнопке блока «Ссылка» (`api/cabinet_tracker.py::go_link_block`,
    владелец 03.10.2026): адрес занятия уходит ученику только через сервер, и
    сервер повторяет очередь ленты. Иначе ученик, переславший кнопку, пустил бы
    на занятие однокурсника, который ещё не сдал домашку.

    Очередь сквозная по всему окну ленты, поэтому считает её та же
    `feed_for_student`, что рисует экран, — своей копии условия здесь нет.
    Окна два: лента цикла задания и текущая — датное задание закончившегося
    цикла ученик видит и в идущем (`task_is_archived_for_user`). Открыт хоть в
    одном — открыт: экран в нём тоже рисует кнопку живой. Побочные эффекты те
    же, что у показа ленты (окна портфолио), а ученик её только что видел.
    """
    cycle_ids = [task.topic_id, None] if task.topic_id is not None else [None]
    for cycle_id in cycle_ids:
        feed = feed_for_student(
            db, user_id=user_id, user_tariff=user_tariff, today=today, cycle_id=cycle_id,
        )
        for step in feed["steps"]:
            block = step["block"]
            if (
                block is not None
                and block.id == block_id
                and step["status"] in (STATUS_CURRENT, STATUS_DONE)
            ):
                return True
    return False


# Порядок вкладок и подписи — созвон 30.09.2026 («общая, композиция или
# рисунок, то есть три вкладки»). Значение — то, что лежит в `subject` шага.
SUBJECT_TAB_GENERAL = ("", "Общее")
SUBJECT_TAB_ORDER = ("Композиция", "Рисунок")


def _subject_tabs(steps: list[dict]) -> list[dict]:
    present = {step["subject"] for step in steps if step["subject"]}
    if not present:
        return []
    # Предмет вне привычной пары (если появится) не теряется — встаёт в конец.
    subjects = list(SUBJECT_TAB_ORDER) + sorted(present - set(SUBJECT_TAB_ORDER))
    return [{"value": SUBJECT_TAB_GENERAL[0], "label": SUBJECT_TAB_GENERAL[1]}] + [
        {"value": subject, "label": subject} for subject in subjects
    ]
