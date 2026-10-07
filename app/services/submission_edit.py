"""Единые границы правки ученических сдач."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.homework_feedback import HomeworkFeedback, HomeworkFeedbackMessage
from app.models.homework_submission import STATUS_ACCEPTED, STATUS_NEEDS_REVISION, HomeworkSubmission
from app.models.task_block import (
    LATE_SUBMISSION_BLOCK_TYPES, TaskBlock, TaskBlockSubmission,
)
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.models.tracker import TrackerTask


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def deadline_reason(
    task: TrackerTask, block: TaskBlock | None = None, *,
    user_tariff: str | None = None,
    tariff_deadlines: dict[str, datetime | None] | None = None,
    task_tariff_deadlines: dict[str, datetime | None] | None = None,
    now: datetime | None = None,
    late_allowed: bool = False,
) -> str | None:
    """Почему правка закрыта по сроку — или `None`, если срок не вышел.

    **Первую сдачу срок не запирает** (владелец 06.10.2026: «досдать свыше
    срока всегда можно, но просрок дедлайна записывается и показан при
    проверке задания, также в статистике. ЭТО ПРАВИЛО!!!»). Такой вызов идёт
    с `late_allowed=True`: срок сдачи и день задания пропускаются, запирает
    только закрытие блока `closes_at`. Без `late_allowed` — правка уже
    сданного, её срок по-прежнему запирает: опоздание пишется по первой
    сдаче, и замена после срока спрятала бы его.

    Источники срока, берётся самый ранний наступивший: день задания
    (`TrackerTask.due_at`), закрытие блока по календарю (`closes_at`) и срок
    сдачи (`submit_until`, владелец 27.09.2026). Последний живёт на двух
    этажах — у блока и у задания, — и у каждого может быть свой вариант под
    тариф ученика; какой из четырёх действует, решает
    `task_blocks.submit_deadline_for`, и второй копии этого правила здесь не
    появляется.

    Текст срок не называет (владелец 02.10.2026). Раньше называл — «срок
    истёк» без даты вызывал вопрос «а когда он был», — но `submit_until`
    ставят с запасом, и дата сайта («04.10 в 19:23») расходилась с дедлайном
    из текста задания («30 сентября»): ученики решили, что срок перенесли.
    Дедлайн ученику называет только текст задания.

    **День задания — срок, только пока срока сдачи нет** (владелец 28.09.2026).
    Задание с экрана дня несёт `due_at` = 23:59 своего дня, и раньше он
    запирал вместе со сроком сдачи — по более раннему из двух: срок «до 30.09
    21:00» у задания 25.09 закрывал приём в 25.09 23:59, продлить его за конец
    дня было нельзя, а лента при этом показывала 30.09. Теперь если срок
    сдачи настроен где угодно (`task_blocks.submit_deadline_is_set`, включая
    «бессрочно» у тарифа), запирает он один; иначе — по-старому, день задания.

    `late_allowed` — сдача, которую после срока принимают опозданием
    (`LATE_SUBMISSION_BLOCK_TYPES`, решает `block_work_reason`): срок сдачи и
    день задания её не запирают, запирает только закрытие блока `closes_at`.
    """
    moment = now or datetime.now(timezone.utc)
    deadline = upload_deadline(
        task, block, user_tariff=user_tariff,
        tariff_deadlines=tariff_deadlines,
        task_tariff_deadlines=task_tariff_deadlines,
        late_allowed=late_allowed,
    )
    if deadline is not None and deadline <= moment:
        return "Срок сдачи прошёл. Изменить работу нельзя."
    return None


def upload_deadline(
    task: TrackerTask, block: TaskBlock | None = None, *,
    user_tariff: str | None = None,
    tariff_deadlines: dict[str, datetime | None] | None = None,
    task_tariff_deadlines: dict[str, datetime | None] | None = None,
    late_allowed: bool = False,
) -> datetime | None:
    """Срок сдачи **этого** ученика — самый ранний из источников
    `deadline_reason` (правила — в его докстринге); `None` — бессрочно. После
    него первую сдачу примут опозданием, а сданное уже не поменять. Вынесено 29.09.2026 ради напоминаний о сроке
    (`services/student_reminders.py`): им нужен тот же момент заранее, а не
    отказ после, и второй копии правила там быть не должно.
    """
    from app.services.task_blocks import submit_deadline_for, submit_deadline_is_set

    sources = dict(
        user_tariff=user_tariff,
        block_overrides=tariff_deadlines,
        task_overrides=task_tariff_deadlines,
    )
    # `block=None` — домашка: срок задания и его строки тарифа, те же правила.
    deadlines = [] if late_allowed else [submit_deadline_for(block, task, **sources)]
    if block is not None:
        deadlines.append(block.closes_at)
    if not late_allowed and not submit_deadline_is_set(block, task, **sources):
        deadlines.append(task.due_at)
    present = [_utc(value) for value in deadlines if value]
    return min(present) if present else None


def late_first_submission(block: TaskBlock, submission: TaskBlockSubmission | None) -> bool:
    """Примут ли эту сдачу после срока (опозданием), а не откажут.

    Первую сдачу любого блока сдачи (владелец 06.10.2026: «досдать свыше
    срока всегда можно»; до этого с 30.09.2026 — только контрольной на
    время). Строка сдачи без `submitted_at` — оборванная загрузка, работы в
    ней ещё нет. Уже отправленную работу после срока не заменить: опоздание
    пишется по первой сдаче, и замена спрятала бы его
    (`LATE_SUBMISSION_BLOCK_TYPES`).
    """
    return block.block_type in LATE_SUBMISSION_BLOCK_TYPES and (
        submission is None or submission.submitted_at is None
    )


def block_work_reason(
    db: Session, task: TrackerTask, block: TaskBlock,
    submission: TaskBlockSubmission | None, *,
    user_tariff: str | None = None,
    tariff_deadlines: dict[str, datetime | None] | None = None,
    task_tariff_deadlines: dict[str, datetime | None] | None = None,
    now: datetime | None = None,
) -> str | None:
    # Явный возврат сотрудника открывает замену даже после срока. Иначе
    # «Вернуть на доработку» показывалось бы, но ученик всё равно получил 409.
    if submission is not None and submission.needs_revision:
        return None
    # Сроки по тарифам читаем сами, если их не передали: мутирующие роуты
    # сдачи зовут эту функцию по одному блоку, и заставлять каждый помнить про
    # предзагрузку обоих этажей — способ однажды забыть и молча пустить работу
    # после срока. Лента передаёт готовые словари, чтобы не ходить в базу на
    # каждый блок.
    if tariff_deadlines is None:
        from app.services.task_blocks import get_submit_deadlines
        tariff_deadlines = get_submit_deadlines(db, [block.id]).get(block.id)
    if task_tariff_deadlines is None:
        from app.services.task_blocks import get_task_submit_deadlines
        task_tariff_deadlines = get_task_submit_deadlines(db, [task.id]).get(task.id)
    reason = deadline_reason(
        task, block, user_tariff=user_tariff,
        tariff_deadlines=tariff_deadlines,
        task_tariff_deadlines=task_tariff_deadlines, now=now,
        late_allowed=late_first_submission(block, submission),
    )
    if reason or submission is None:
        return reason
    # Сданную работу запирает только обратная связь преподавателя (владелец
    # 07.10.2026: «закрыть смену фото только после ОС от преподавателя»).
    # Статус задания «выполнено» замену не трогает: с 02.10.2026 он её запирал,
    # и в задании, где сдача — последний шаг, «Заменить фото» пропадало сразу
    # после отправки, до всякой проверки (цикл «Октябрь», «Рисунок»: 28 из 31
    # непроверенных работ).
    if submission.reviewed_at is not None or submission.score is not None:
        return "Преподаватель уже проверил работу. Изменить её нельзя."
    replied_query = (
        db.query(TaskBlockFeedbackMessage.id)
        .join(TaskBlockFeedback, TaskBlockFeedback.id == TaskBlockFeedbackMessage.feedback_id)
        .filter(TaskBlockFeedback.submission_id == submission.id,
                TaskBlockFeedbackMessage.sender_role != "student")
    )
    if submission.submitted_at is not None:
        # Считать «ответил» только сообщения после текущей сдачи — иначе
        # старое сообщение куратора (например, само возвращение на
        # доработку) навсегда блокирует следующую попытку правки.
        replied_query = replied_query.filter(
            TaskBlockFeedbackMessage.created_at > submission.submitted_at
        )
    if replied_query.first() or submission.review_comment:
        return "Преподаватель уже ответил по работе. Изменить её нельзя."
    return None


def homework_reason(
    db: Session, task: TrackerTask, submission: HomeworkSubmission | None,
    *, user_tariff: str | None = None,
    task_tariff_deadlines: dict[str, datetime | None] | None = None,
    now: datetime | None = None,
) -> str | None:
    if submission is not None and submission.status == STATUS_NEEDS_REVISION:
        return None
    # Срок — по тем же правилам, что у блоков сдачи (владелец 28.09.2026):
    # срок задания и его строка тарифа, день задания — только если срока нет.
    # До этого домашка сверялась с одним днём задания. Первую сдачу срок не
    # запирает, как у блоков (владелец 06.10.2026), — запирает замену.
    if task_tariff_deadlines is None:
        from app.services.task_blocks import get_task_submit_deadlines
        task_tariff_deadlines = get_task_submit_deadlines(db, [task.id]).get(task.id)
    reason = deadline_reason(
        task, user_tariff=user_tariff,
        task_tariff_deadlines=task_tariff_deadlines, now=now,
        late_allowed=submission is None or submission.submitted_at is None,
    )
    if reason or submission is None:
        return reason
    if submission.status == STATUS_ACCEPTED:
        return "Работа уже принята. Изменить её нельзя."
    replied_query = (
        db.query(HomeworkFeedbackMessage.id)
        .join(HomeworkFeedback, HomeworkFeedback.id == HomeworkFeedbackMessage.feedback_id)
        .filter(HomeworkFeedback.submission_id == submission.id,
                HomeworkFeedbackMessage.sender_role != "student")
    )
    if submission.submitted_at is not None:
        replied_query = replied_query.filter(
            HomeworkFeedbackMessage.created_at > submission.submitted_at
        )
    if replied_query.first():
        return "Преподаватель уже ответил по работе. Изменить её нельзя."
    return None
