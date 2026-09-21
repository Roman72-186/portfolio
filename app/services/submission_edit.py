"""Единые границы правки ученических сдач."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.homework_feedback import HomeworkFeedback, HomeworkFeedbackMessage
from app.models.homework_submission import STATUS_ACCEPTED, STATUS_NEEDS_REVISION, HomeworkSubmission
from app.models.task_block import TaskBlock, TaskBlockSubmission
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.models.tracker import TrackerTask


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def deadline_reason(task: TrackerTask, block: TaskBlock | None = None, *, now: datetime | None = None) -> str | None:
    moment = now or datetime.now(timezone.utc)
    deadlines = [value for value in (task.due_at, block.closes_at if block else None) if value]
    if deadlines and min(_utc(value) for value in deadlines) <= moment:
        return "Срок сдачи истёк. Изменить работу нельзя."
    return None


def block_work_reason(
    db: Session, task: TrackerTask, block: TaskBlock,
    submission: TaskBlockSubmission | None, *, now: datetime | None = None,
) -> str | None:
    reason = deadline_reason(task, block, now=now)
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
    *, now: datetime | None = None,
) -> str | None:
    reason = deadline_reason(task, now=now)
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
