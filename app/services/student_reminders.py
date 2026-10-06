"""Напоминания ученику по расписанию (владелец 29.09.2026).

Четыре события, у которых нет «кнопки», в момент нажатия которой можно было
бы послать уведомление, — их замечает планировщик раз в полчаса
(`exam_scheduler.py`, job `student_reminders`):

1. **Новое задание** в ленте. Задание становится видно ученику в самый
   поздний из моментов: публикация задания (у конструктора `published_at`
   не ставится — берём `created_at`), открытие его темы (цикл, этап или
   служебная тема дня) и публикация темы, собственное `starts_at`. Кому
   видно — решает `tracker.task_audience_user_ids`, тот же путь, что у
   статистики; своей копии адресации здесь нет. Пробник не шлём: у него
   своё напоминание «Пробник через 3 дня» (`exam_scheduler`).
2. **Новое видео.** Видео у ученика живёт только внутри заданий (каталога в
   меню нет). Задание с роликом приходит одним уведомлением «Новый
   видеоурок» (владелец: «одно на задание»). Отдельно — только ролик,
   добавленный в задание, которое ученик уже видел: иначе запись занятия,
   доложенная в открытое задание, прошла бы молча.
3. **Срок сдачи** — за сутки и за 3 часа (владелец), если блок сдачи или
   ответа (`DEADLINE_BLOCKS_COMPLETION`) или контрольная на время
   (`LATE_SUBMISSION_BLOCK_TYPES` — после срока её примут, но опозданием)
   не закрыты или домашка не сдана.
   Момент срока — `submission_edit.upload_deadline`, та же функция, что
   запирает сдачу: напоминание не может разойтись с настоящим сроком.
4. **Конец доступа** (`User.access_until`) — за 3 дня и за сутки.
5. **Долг цикла** (владелец 06.10.2026: «отправить уведомление, что цикл
   закроется и у него есть долг… Напоминания должны прийти»). Цикл, на
   котором ученик стоит (`tracker.effective_cycle`, галочка «не пускать
   дальше»), с незакрытыми обязательными заданиями: за 3 часа до срока цикла
   и после срока раз в день, пока долг не закрыт. Срок — «по» тарифа или
   конец цикла (`tracker.cycle_deadline_for`). Ежедневное уходит только в
   прогон с 10:00 до 11:00 МСК — не ночью — и делит ключ с кнопкой «Напомнить
   всем» (`cycle_stats.remind_cycle_debtors`): в один день одно сообщение.
   Долги со сроком до 06.10.2026 («Предобучение») автоматически не
   напоминаются (`CYCLE_DEBT_REMINDERS_SINCE`).

Повторов нет: каждое отправленное событие оставляет строку `StudentReminder`.
Ключ срока несёт сам момент — продлили срок, напоминание придёт заново.

Первый запуск не заваливает учеников старым: новое задание и видео ищутся
только за последние `LOOKBACK` часов. Несколько новых заданий в одном прогоне
(цикл открывается десятком заданий сразу) уходят одним сообщением со списком.

Ночной паузы пока нет: владелец хочет копить до 9:00 **в часовом поясе
ученика** и отложил это отдельной задачей.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.cache import invalidate_unread
from app.models.homework_submission import HomeworkSubmission
from app.models.learning_topic import LearningTopic
from app.models.notification import Notification
from app.models.student_reminder import (
    KIND_ACCESS_1D,
    KIND_ACCESS_3D,
    KIND_CYCLE_CLOSING_3H,
    KIND_CYCLE_DEBT,
    KIND_DEADLINE_3H,
    KIND_DEADLINE_24H,
    KIND_NEW_TASK,
    KIND_NEW_VIDEO,
    StudentReminder,
)
from app.models.task_block import (
    DEADLINE_BLOCKS_COMPLETION,
    LATE_SUBMISSION_BLOCK_TYPES,
    VIDEO_BLOCK_TYPES,
    TaskBlock,
    TaskBlockState,
    TaskBlockTariffDeadline,
)
from app.models.tracker import (
    ITEM_MOCK_EXAM,
    ITEM_VIDEO,
    SOURCE_HOMEWORK,
    STATUS_DONE,
    TrackerTask,
    TrackerTaskState,
    TrackerTaskTariffDeadline,
)
from app.models.user import User
from app.services.submission_edit import upload_deadline
from app.services.task_blocks import (
    get_blocks_for_tasks,
    get_submit_deadlines,
    get_tariffs,
    feed_visible_blocks,
    get_task_submit_deadlines,
)
from app.models.learning_topic import TOPIC_KIND_WEEK
from app.services.cycle_stats import reminder_message
from app.services.program import day_bounds, msk_date
from app.services.tracker import (
    cycle_bounds,
    cycle_deadline_for,
    cycle_label,
    effective_cycle,
    missing_required_tasks,
    program_learners,
    program_students,
    task_audience_user_ids,
)
from app.services.video_topics import cycle_tariff_closes
from app.services.tz import MSK_TZ

logger = logging.getLogger(__name__)

# Насколько назад ищем новое задание и видео. Больше интервала прогона
# (30 минут) с запасом на перезапуск контейнера при деплое; повторы гасит
# `StudentReminder`, так что запас ничего не дублирует.
LOOKBACK = timedelta(hours=6)

# Сроки сдачи: чем ближе, тем раньше в кортеже — ближайшая ступень главнее.
DEADLINE_STAGES = (
    (timedelta(hours=3), KIND_DEADLINE_3H),
    (timedelta(hours=24), KIND_DEADLINE_24H),
)
ACCESS_STAGES = (
    (timedelta(days=1), KIND_ACCESS_1D),
    (timedelta(days=3), KIND_ACCESS_3D),
)

# Сколько названий печатать в сводке, дальше — «и ещё N».
SUMMARY_LIMIT = 8

# Долг цикла: за сколько до срока предупредить и в какой час МСК слать
# ежедневное после срока (прогон раз в 30 минут — попадают два).
CYCLE_CLOSING_AHEAD = timedelta(hours=3)
CYCLE_DEBT_DAILY_HOUR = 10
# Ежедневное — только по долгам, чей срок прошёл после включения рассылки
# (владелец 06.10.2026: «предобучение нужно исключить»). Все циклы
# «Предобучения» закончились к 04.10.2026; их должникам напоминают только
# кнопкой «Напомнить всем». Граница — дата, а не название периода: так она не
# ломается от переименования и не требует id в коде.
CYCLE_DEBT_REMINDERS_SINCE = datetime(2026, 10, 6, tzinfo=MSK_TZ)


def _utc(value: datetime | None) -> datetime | None:
    """Наивное время из базы — UTC (SQLite в тестах отдаёт без таймзоны)."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _short_msk(value: datetime) -> str:
    """«30.09 в 18:00» — год в уведомлении о ближайших сутках лишний."""
    return _utc(value).astimezone(MSK_TZ).strftime("%d.%m в %H:%M")


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


@dataclass
class _Item:
    """Одно событие для одного ученика до сборки сообщения."""
    kind: str
    ref: str
    title: str          # название задания
    moment: datetime | None = None  # срок сдачи или конец доступа
    has_video: bool = False
    tasks: list[str] | None = None  # долг цикла: незакрытые обязательные


# Кто вообще получает — `tracker.program_students` / `program_learners`,
# какие блоки ученик видит — `task_blocks.feed_visible_blocks`. Вынесены
# 03.10.2026: по тем же правилам статистика считает, кто не сдал задание.

def _has_video(block: TaskBlock) -> bool:
    return block.block_type in VIDEO_BLOCK_TYPES and block.video_id is not None


# ── 1–2. Новое задание и новое видео ────────────────────────────────────────

def _task_opened_at(task: TrackerTask, topic: LearningTopic | None) -> datetime:
    moments = [_utc(task.published_at) or _utc(task.created_at), _utc(task.starts_at)]
    if topic is not None:
        moments += [_utc(topic.opens_at), _utc(topic.published_at)]
    return max(m for m in moments if m is not None)


def _collect_new_content(
    db: Session, learners: dict[int, User], now: datetime,
    items: dict[int, list[_Item]],
) -> None:
    since = now - LOOKBACK
    base = (
        db.query(TrackerTask, LearningTopic)
        .outerjoin(LearningTopic, LearningTopic.id == TrackerTask.topic_id)
        .filter(
            TrackerTask.is_published.is_(True),
            TrackerTask.deleted_at.is_(None),
            TrackerTask.kind != ITEM_MOCK_EXAM,
        )
    )
    # Кандидаты грубо, момент открытия — точно в питоне ниже: «максимум из
    # четырёх моментов» одним SQL на двух диалектах не выразить.
    task_rows = base.filter(or_(
        TrackerTask.published_at > since,
        TrackerTask.created_at > since,
        TrackerTask.starts_at > since,
        LearningTopic.opens_at > since,
        LearningTopic.published_at > since,
    )).all()
    video_block_rows = (
        base.join(TaskBlock, TaskBlock.task_id == TrackerTask.id)
        .filter(
            TaskBlock.block_type.in_(VIDEO_BLOCK_TYPES),
            TaskBlock.video_id.isnot(None),
            or_(TaskBlock.created_at > since, TaskBlock.opens_at > since),
        )
        .add_columns(TaskBlock)
        .all()
    )

    new_tasks = {}
    for task, topic in task_rows:
        opened = _task_opened_at(task, topic)
        if since < opened <= now:
            new_tasks[task.id] = task
    # Ролики, ставшие видны в окне: блок моложе задания или открылся позже.
    videos: dict[int, list[tuple[TaskBlock, datetime]]] = defaultdict(list)
    tasks_by_id = dict(new_tasks)
    for task, topic, block in video_block_rows:
        block_opened = max(
            m for m in (
                _task_opened_at(task, topic), _utc(block.created_at), _utc(block.opens_at),
            )
            if m is not None
        )
        if since < block_opened <= now:
            videos[task.id].append((block, block_opened))
            tasks_by_id.setdefault(task.id, task)
    if not tasks_by_id:
        return

    task_ids = list(tasks_by_id)
    blocks_by_task = get_blocks_for_tasks(db, task_ids)
    tariffs = get_tariffs(
        db, [b.id for blocks in blocks_by_task.values() for b in blocks]
    )
    # Когда ученику уже написали о задании — от этого момента ролик считается
    # доложенным позже, а не частью самого задания.
    told_at = {
        (r.user_id, int(r.ref)): _utc(r.created_at)
        for r in db.query(StudentReminder).filter(
            StudentReminder.kind == KIND_NEW_TASK,
            StudentReminder.ref.in_([str(t) for t in task_ids]),
        ).all()
    }

    for task_id, task in tasks_by_id.items():
        audience = task_audience_user_ids(db, task_id) & learners.keys()
        for uid in audience:
            user = learners[uid]
            visible = feed_visible_blocks(blocks_by_task.get(task_id, []), tariffs, user.tariff)
            if blocks_by_task.get(task_id) and not visible:
                continue  # всё задание — чужого тарифа
            told = told_at.get((uid, task_id))
            if task_id in new_tasks and told is None:
                # Одно уведомление на задание: ролики внутри идут в заголовок.
                # Только уже открытые — ролик с `opens_at` завтра придёт завтра
                # своим «Новое видео», а «Новый видеоурок» сегодня звал бы
                # смотреть то, что ещё закрыто.
                items[uid].append(_Item(
                    kind=KIND_NEW_TASK, ref=str(task_id), title=task.title,
                    has_video=task.kind == ITEM_VIDEO or any(
                        _has_video(b) and not (_utc(b.opens_at) and _utc(b.opens_at) > now)
                        for b in visible
                    ),
                ))
                continue
            visible_ids = {b.id for b in visible}
            for block, block_opened in videos.get(task_id, []):
                if block.id not in visible_ids:
                    continue
                if told is not None and block_opened <= told:
                    continue  # ролик был в задании, когда о нём написали
                items[uid].append(_Item(
                    kind=KIND_NEW_VIDEO, ref=str(block.id), title=task.title,
                ))


# ── 3. Срок сдачи ───────────────────────────────────────────────────────────

def _deadline_candidates(db: Session, now: datetime, horizon: datetime) -> list[int]:
    """Задания, у которых какой-то из источников срока попадает в окно.
    Точный срок ученика считает `upload_deadline` ниже — здесь только отбор."""
    window = lambda col: col.between(now, horizon)  # noqa: E731
    ids: set[int] = set()
    ids.update(r[0] for r in db.query(TrackerTask.id).filter(or_(
        window(TrackerTask.submit_until), window(TrackerTask.due_at),
    )).all())
    ids.update(r[0] for r in db.query(TrackerTaskTariffDeadline.task_id).filter(
        window(TrackerTaskTariffDeadline.submit_until),
    ).all())
    ids.update(r[0] for r in db.query(TaskBlock.task_id).filter(or_(
        window(TaskBlock.submit_until), window(TaskBlock.closes_at),
    )).all())
    ids.update(r[0] for r in (
        db.query(TaskBlock.task_id)
        .join(TaskBlockTariffDeadline, TaskBlockTariffDeadline.block_id == TaskBlock.id)
        .filter(window(TaskBlockTariffDeadline.submit_until))
        .all()
    ))
    return sorted(ids)


def _collect_deadlines(
    db: Session, learners: dict[int, User], now: datetime,
    items: dict[int, list[_Item]],
) -> None:
    horizon = now + DEADLINE_STAGES[-1][0]
    task_ids = _deadline_candidates(db, now, horizon)
    if not task_ids:
        return
    tasks = {
        t.id: t for t in db.query(TrackerTask).filter(
            TrackerTask.id.in_(task_ids),
            TrackerTask.is_published.is_(True),
            TrackerTask.deleted_at.is_(None),
            TrackerTask.kind != ITEM_MOCK_EXAM,
        ).all()
    }
    blocks_by_task = {
        task_id: [
            b for b in blocks
            if b.block_type in DEADLINE_BLOCKS_COMPLETION + LATE_SUBMISSION_BLOCK_TYPES
        ]
        for task_id, blocks in get_blocks_for_tasks(db, list(tasks)).items()
    }
    block_ids = [b.id for blocks in blocks_by_task.values() for b in blocks]
    tariffs = get_tariffs(db, block_ids)
    block_deadlines = get_submit_deadlines(db, block_ids)
    task_deadlines = get_task_submit_deadlines(db, list(tasks))

    for task_id, task in tasks.items():
        if _utc(task.starts_at) and _utc(task.starts_at) > now:
            continue
        blocks = blocks_by_task.get(task_id, [])
        is_homework = task.source_kind == SOURCE_HOMEWORK
        if not blocks and not is_homework:
            continue  # сдавать нечего — видео, текст, материалы
        audience = task_audience_user_ids(db, task_id) & learners.keys()
        if not audience:
            continue
        done_blocks = {
            (s.user_id, s.block_id) for s in db.query(TaskBlockState).filter(
                TaskBlockState.block_id.in_([b.id for b in blocks] or [-1]),
                TaskBlockState.user_id.in_(audience),
                TaskBlockState.status == STATUS_DONE,
            ).all()
        }
        done_tasks = {
            r[0] for r in db.query(TrackerTaskState.user_id).filter(
                TrackerTaskState.task_id == task_id,
                TrackerTaskState.user_id.in_(audience),
                TrackerTaskState.status == STATUS_DONE,
            ).all()
        }
        handed_in = set()
        if is_homework:
            handed_in = {
                r[0] for r in db.query(HomeworkSubmission.user_id).filter(
                    HomeworkSubmission.tracker_task_id == task_id,
                    HomeworkSubmission.user_id.in_(audience),
                    HomeworkSubmission.submitted_at.isnot(None),
                ).all()
            }

        for uid in audience:
            if uid in done_tasks:
                continue
            user = learners[uid]
            pending: list[datetime] = []
            for block in feed_visible_blocks(blocks, tariffs, user.tariff):
                if (uid, block.id) in done_blocks:
                    continue
                if _utc(block.opens_at) and _utc(block.opens_at) > now:
                    continue
                pending.append(upload_deadline(
                    task, block, user_tariff=user.tariff,
                    tariff_deadlines=block_deadlines.get(block.id),
                    task_tariff_deadlines=task_deadlines.get(task_id),
                ))
            if is_homework and uid not in handed_in:
                pending.append(upload_deadline(
                    task, None, user_tariff=user.tariff,
                    task_tariff_deadlines=task_deadlines.get(task_id),
                ))
            upcoming = [d for d in pending if d is not None and now < d <= horizon]
            if not upcoming:
                continue
            deadline = min(upcoming)
            kind = _stage(deadline - now, DEADLINE_STAGES)
            items[uid].append(_Item(
                kind=kind, ref=f"{task_id}:{deadline.isoformat()}",
                title=task.title, moment=deadline,
            ))


def _stage(left: timedelta, stages) -> str:
    for limit, kind in stages:
        if left <= limit:
            return kind
    return stages[-1][1]


# ── 4. Конец доступа ────────────────────────────────────────────────────────

def _collect_access(
    students: dict[int, User], now: datetime, items: dict[int, list[_Item]],
) -> None:
    horizon = now + ACCESS_STAGES[-1][0]
    for uid, user in students.items():
        until = _utc(user.access_until)
        if until is None or not now < until <= horizon:
            continue
        kind = _stage(until - now, ACCESS_STAGES)
        items[uid].append(_Item(
            kind=kind, ref=until.isoformat(), title="", moment=until,
        ))


# ── 5. Долг цикла ───────────────────────────────────────────────────────────

def _collect_cycle_debts(
    db: Session, learners: dict[int, User], now: datetime,
    items: dict[int, list[_Item]],
) -> None:
    today = msk_date(now)
    daily = now.astimezone(MSK_TZ).hour == CYCLE_DEBT_DAILY_HOUR
    horizon = now + CYCLE_CLOSING_AHEAD
    cycles = db.query(LearningTopic).filter(
        LearningTopic.kind == TOPIC_KIND_WEEK,
        LearningTopic.deleted_at.is_(None),
        LearningTopic.is_published.is_(True),
        LearningTopic.locks_next.is_(True),
        LearningTopic.opens_at <= now,
    ).all()
    closes = cycle_tariff_closes(db, [topic.id for topic in cycles])
    # Дорогой обход учеников (текущий цикл и долг каждого) — только когда
    # есть что слать: ежедневный час или срок какого-то цикла в ближайшие 3
    # часа (общий конец или «по» любого тарифа).
    soon = any(
        now < moment <= horizon
        for topic in cycles
        for moment in [day_bounds(cycle_bounds(topic)[1])[1], *closes.get(topic.id, {}).values()]
    )
    if not daily and not soon:
        return
    for uid, user in learners.items():
        topic = effective_cycle(db, uid, today)
        if topic is None or not topic.locks_next:
            continue
        deadline = cycle_deadline_for(topic, user.tariff, closes.get(topic.id))
        if now < deadline <= horizon:
            kind, ref = KIND_CYCLE_CLOSING_3H, f"{topic.id}:{deadline.isoformat()}"
        elif deadline <= now and daily and deadline >= CYCLE_DEBT_REMINDERS_SINCE:
            # Ключ как у кнопки «Напомнить всем» (`cycle_stats._reminder_ref`).
            kind, ref = KIND_CYCLE_DEBT, f"{topic.id}:{today.isoformat()}"
        else:
            continue
        missing = missing_required_tasks(db, uid, topic)
        if not missing:
            continue
        items[uid].append(_Item(
            kind=kind, ref=ref, title=cycle_label(db, topic), moment=deadline,
            tasks=[task.title for task in missing],
        ))


# ── Сборка сообщений ────────────────────────────────────────────────────────

def _titles(items: list[_Item]) -> str:
    lines = [f"«{item.title}»" for item in items[:SUMMARY_LIMIT]]
    rest = len(items) - SUMMARY_LIMIT
    if rest > 0:
        lines.append(f"и ещё {rest}")
    return "\n".join(lines)


def _content_message(items: list[_Item]) -> tuple[str, str]:
    if len(items) == 1:
        item = items[0]
        if item.kind == KIND_NEW_VIDEO:
            return f"Новое видео в задании «{item.title}»", "Смотри в ленте обучения."
        if item.has_video:
            return f"Новый видеоурок: «{item.title}»", "Уже в ленте обучения."
        return f"Новое задание: «{item.title}»", "Уже в ленте обучения."
    return f"Новое в ленте обучения: {len(items)}", _titles(items)


def _deadline_message(items: list[_Item]) -> tuple[str, str]:
    """Дату срока не называем (владелец 02.10.2026): `submit_until` ставят с
    запасом, и «до 04.10 в 19:23» спорило бы с дедлайном из текста задания —
    ученики приняли бы это за перенос. Дедлайн называет только задание."""
    if len(items) == 1:
        item = items[0]
        left = (
            "Осталось меньше трёх часов." if item.kind == KIND_DEADLINE_3H
            else "Осталось меньше суток."
        )
        return f"Скоро закроется приём работ: «{item.title}»", left
    word = _plural(len(items), "задание", "задания", "заданий")
    items = sorted(items, key=lambda i: i.moment)
    return f"Скоро закроется приём работ: {len(items)} {word}", _titles(items)


def _access_message(item: _Item) -> tuple[str, str]:
    title = f"Доступ к урокам закроется {_short_msk(item.moment)}"
    if item.kind == KIND_ACCESS_1D:
        return title, "Остался последний день. Оплати обучение, и уроки останутся открытыми."
    return title, "Осталось три дня. Оплати обучение, и уроки останутся открытыми."


def _cycle_debt_message(item: _Item) -> tuple[str, str]:
    """Срок цикла не называем — по той же причине, что у срока сдачи
    (`_deadline_message`): дедлайн называет только задание."""
    names = ", ".join(f"«{title}»" for title in item.tasks or [])
    cycle = item.title if item.title.lower().startswith("цикл") else f"Цикл «{item.title}»"
    if item.kind == KIND_CYCLE_CLOSING_3H:
        return (
            f"{cycle} закрывается через 3 часа",
            f"Осталось: {names}. Успей сдать до конца срока – после него "
            "работа запишется как сданная позже.",
        )
    title, text = reminder_message(item.title, [_Named(t) for t in item.tasks or []])
    return title, f"{text} Срок цикла прошёл – сданное сейчас запишется как сданное позже."


@dataclass
class _Named:
    """`reminder_message` ждёт задания, ему нужно только название."""
    title: str


def _messages(items: list[_Item]) -> list[tuple[str, str]]:
    content = [i for i in items if i.kind in (KIND_NEW_TASK, KIND_NEW_VIDEO)]
    deadlines = [i for i in items if i.kind in (KIND_DEADLINE_24H, KIND_DEADLINE_3H)]
    access = [i for i in items if i.kind in (KIND_ACCESS_3D, KIND_ACCESS_1D)]
    out = []
    if content:
        out.append(_content_message(content))
    if deadlines:
        out.append(_deadline_message(deadlines))
    for item in access:
        out.append(_access_message(item))
    for item in items:
        if item.kind in (KIND_CYCLE_CLOSING_3H, KIND_CYCLE_DEBT):
            out.append(_cycle_debt_message(item))
    return out


def collect(db: Session, now: datetime) -> dict[int, list[_Item]]:
    """Что каждому ученику пора сообщить — без уже отправленного."""
    students = program_students(db, now)
    learners = program_learners(students)
    raw: dict[int, list[_Item]] = defaultdict(list)
    _collect_new_content(db, learners, now, raw)
    _collect_deadlines(db, learners, now, raw)
    _collect_access(students, now, raw)
    _collect_cycle_debts(db, learners, now, raw)

    all_items = [item for bucket in raw.values() for item in bucket]
    sent = set()
    if all_items:
        rows = db.query(
            StudentReminder.user_id, StudentReminder.kind, StudentReminder.ref,
        ).filter(
            StudentReminder.user_id.in_(list(raw)),
            StudentReminder.kind.in_({i.kind for i in all_items}),
        ).all()
        sent = {(r[0], r[1], r[2]) for r in rows}
    result: dict[int, list[_Item]] = {}
    for uid, bucket in raw.items():
        seen = set()
        fresh = []
        for item in bucket:
            key = (uid, item.kind, item.ref)
            if key in sent or key in seen:
                continue
            seen.add(key)
            fresh.append(item)
        if fresh:
            result[uid] = fresh
    return result


def run_student_reminders(db: Session, now: datetime | None = None) -> list[int]:
    """Создать уведомления и отметки об отправке. Коммитит сам; возвращает
    id уведомлений — рассылку в Telegram и push делает вызывающий после
    коммита (`notify_many_sync`), как у остальных job планировщика."""
    now = _utc(now) or datetime.now(timezone.utc)
    pending = collect(db, now)
    created: list[Notification] = []
    for uid, items in pending.items():
        for title, text in _messages(items):
            notif = Notification(user_id=uid, title=title[:200], text=text)
            db.add(notif)
            created.append(notif)
        for item in items:
            db.add(StudentReminder(user_id=uid, kind=item.kind, ref=item.ref))
    db.commit()
    for uid in pending:
        invalidate_unread(uid)
    if created:
        logger.info(
            "Student reminders: %d уведомлений %d ученикам", len(created), len(pending),
        )
    return [n.id for n in created]
