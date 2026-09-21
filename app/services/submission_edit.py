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
    if submission.reviewed_at is not None or submission.score is not None:
        return "Преподаватель уже проверил работу. Изменить её нельзя."
    replied = (
        db.query(TaskBlockFeedbackMessage.id)
        .join(TaskBlockFeedback, TaskBlockFeedback.id == TaskBlockFeedbackMessage.feedback_id)
        .filter(TaskBlockFeedback.submission_id == submission.id,
                TaskBlockFeedbackMessage.sender_role != "student")
        .first()
    )
    if replied or submission.review_comment:
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
    if submission.status == STATUS_NEEDS_REVISION:
        return "Преподаватель уже проверил работу. Изменить её нельзя."
    replied = (
        db.query(HomeworkFeedbackMessage.id)
        .join(HomeworkFeedback, HomeworkFeedback.id == HomeworkFeedbackMessage.feedback_id)
        .filter(HomeworkFeedback.submission_id == submission.id,
                HomeworkFeedbackMessage.sender_role != "student")
        .first()
    )
    if replied:
        return "Преподаватель уже ответил по работе. Изменить её нельзя."
    return None
