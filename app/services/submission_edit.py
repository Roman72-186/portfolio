"""Единые границы правки ученических сдач."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.homework_feedback import HomeworkFeedback, HomeworkFeedbackMessage
from app.models.homework_submission import STATUS_ACCEPTED, STATUS_NEEDS_REVISION, HomeworkSubmission
from app.models.task_block import TaskBlock, TaskBlockSubmission
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.models.tracker import TrackerTask
from app.services.tz import msk_text


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def deadline_reason(
    task: TrackerTask, block: TaskBlock | None = None, *,
    user_tariff: str | None = None,
    tariff_deadlines: dict[str, datetime | None] | None = None,
    task_tariff_deadlines: dict[str, datetime | None] | None = None,
    now: datetime | None = None,
) -> str | None:
    """Почему правка закрыта по сроку — или `None`, если срок не вышел.

    Источники срока, берётся самый ранний наступивший: день задания
    (`TrackerTask.due_at`), закрытие блока по календарю (`closes_at`) и срок
    сдачи (`submit_until`, владелец 27.09.2026). Последний живёт на двух
    этажах — у блока и у задания, — и у каждого может быть свой вариант под
    тариф ученика; какой из четырёх действует, решает
    `task_blocks.submit_deadline_for`, и второй копии этого правила здесь не
    появляется.

    Текст называет момент по Москве: «срок истёк» без даты вызывал встречный
    вопрос «а когда он был».

    **День задания — срок, только пока срока сдачи нет** (владелец 28.09.2026).
    Задание с экрана дня несёт `due_at` = 23:59 своего дня, и раньше он
    запирал вместе со сроком сдачи — по более раннему из двух: срок «до 30.09
    21:00» у задания 25.09 закрывал приём в 25.09 23:59, продлить его за конец
    дня было нельзя, а лента при этом показывала 30.09. Теперь если срок
    сдачи настроен где угодно (`task_blocks.submit_deadline_is_set`, включая
    «бессрочно» у тарифа), запирает он один; иначе — по-старому, день задания.
    """
    from app.services.task_blocks import submit_deadline_for, submit_deadline_is_set

    moment = now or datetime.now(timezone.utc)
    sources = dict(
        user_tariff=user_tariff,
        block_overrides=tariff_deadlines,
        task_overrides=task_tariff_deadlines,
    )
    # `block=None` — домашка: срок задания и его строки тарифа, те же правила.
    deadlines = [submit_deadline_for(block, task, **sources)]
    if block is not None:
        deadlines.append(block.closes_at)
    if not submit_deadline_is_set(block, task, **sources):
        deadlines.append(task.due_at)
    passed = [_utc(value) for value in deadlines if value and _utc(value) <= moment]
    if passed:
        return f"Срок сдачи истёк {msk_text(min(passed))} по Москве. Изменить работу нельзя."
    return None


def block_work_reason(
    db: Session, task: TrackerTask, block: TaskBlock,
    submission: TaskBlockSubmission | None, *,
    user_tariff: str | None = None,
    tariff_deadlines: dict[str, datetime | None] | None = None,
    task_tariff_deadlines: dict[str, datetime | None] | None = None,
    now: datetime | None = None,
) -> str | None:
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
    )
    if reason or submission is None:
        return reason
    # Куратор явно вернул работу на доработку — правка разрешена, даже если
    # до этого уже была оценка/комментарий (иначе кнопка «Вернуть на
    # доработку» ничего не открывала бы ученику).
    if submission.needs_revision:
        return None
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
    # Срок — по тем же правилам, что у блоков сдачи (владелец 28.09.2026):
    # срок задания и его строка тарифа, день задания — только если срока нет.
    # До этого домашка сверялась с одним днём задания.
    if task_tariff_deadlines is None:
        from app.services.task_blocks import get_task_submit_deadlines
        task_tariff_deadlines = get_task_submit_deadlines(db, [task.id]).get(task.id)
    reason = deadline_reason(
        task, user_tariff=user_tariff,
        task_tariff_deadlines=task_tariff_deadlines, now=now,
    )
    if reason or submission is None:
        return reason
    if submission.status == STATUS_ACCEPTED:
        return "Работа уже принята. Изменить её нельзя."
    # Куратор явно вернул работу на доработку — правка разрешена.
    if submission.status == STATUS_NEEDS_REVISION:
        return None
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
