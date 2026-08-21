"""Сдача домашки учеником: заведение, фото, перезапись финала.

Контракт загрузки — как у пробника (`app/api/cycle_upload.py`): ровно одно
финальное фото + до `MAX_INTERMEDIATE_PER_FINAL` промежуточных, пересдача
перезаписывает финал in-place. Билета и попытки здесь нет — решение
владельца (TODO.md §0 Р2).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case
from sqlalchemy.orm import Session

from app.models.homework_submission import (
    STATUS_SUBMITTED,
    HomeworkSubmission,
    HomeworkSubmissionImage,
)
from app.models.tracker import SOURCE_HOMEWORK, TrackerTask

MAX_INTERMEDIATE_PER_SUBMISSION = 10


def get_submission(db: Session, *, tracker_task_id: int, user_id: int) -> HomeworkSubmission | None:
    return (
        db.query(HomeworkSubmission)
        .filter(
            HomeworkSubmission.tracker_task_id == tracker_task_id,
            HomeworkSubmission.user_id == user_id,
        )
        .one_or_none()
    )


def get_or_create_submission(
    db: Session, *, task: TrackerTask, user_id: int
) -> tuple[HomeworkSubmission, bool]:
    """Завести сдачу под конкретную постановку задачи (не под абстрактное задание).

    `task.source_kind` обязан быть `SOURCE_HOMEWORK` — вызывающий код отвечает
    за то, что это действительно задача типа «домашка».
    """
    existing = get_submission(db, tracker_task_id=task.id, user_id=user_id)
    if existing is not None:
        return existing, False
    submission = HomeworkSubmission(
        homework_id=task.source_id,
        tracker_task_id=task.id,
        user_id=user_id,
        status=STATUS_SUBMITTED,
    )
    db.add(submission)
    db.flush()
    return submission, True


def list_images(db: Session, submission_id: int) -> list[HomeworkSubmissionImage]:
    return (
        db.query(HomeworkSubmissionImage)
        .filter(HomeworkSubmissionImage.submission_id == submission_id)
        .order_by(
            HomeworkSubmissionImage.is_final.desc(),
            HomeworkSubmissionImage.sort_order.asc(),
            HomeworkSubmissionImage.id.asc(),
        )
        .all()
    )


def get_final_image(db: Session, submission_id: int) -> HomeworkSubmissionImage | None:
    return (
        db.query(HomeworkSubmissionImage)
        .filter(
            HomeworkSubmissionImage.submission_id == submission_id,
            HomeworkSubmissionImage.is_final.is_(True),
        )
        .order_by(HomeworkSubmissionImage.id.desc())
        .first()
    )


def count_intermediate_images(db: Session, submission_id: int) -> int:
    return (
        db.query(HomeworkSubmissionImage.id)
        .filter(
            HomeworkSubmissionImage.submission_id == submission_id,
            HomeworkSubmissionImage.is_final.is_(False),
        )
        .count()
    )


def set_final_image(
    db: Session, submission: HomeworkSubmission, *, url: str, path: str | None
) -> HomeworkSubmissionImage:
    """Перезаписать финал: старая S3-картинка не удаляется (как у HomeworkImage —
    та же логика «прибраться ценой чужой сломанной ссылки — плохая сделка»)."""
    db.query(HomeworkSubmissionImage).filter(
        HomeworkSubmissionImage.submission_id == submission.id,
        HomeworkSubmissionImage.is_final.is_(True),
    ).delete(synchronize_session=False)
    image = HomeworkSubmissionImage(
        submission_id=submission.id,
        image_s3_url=url,
        image_s3_path=path,
        is_final=True,
        sort_order=0,
    )
    db.add(image)
    submission.submitted_at = datetime.now(timezone.utc)
    submission.status = STATUS_SUBMITTED
    db.flush()
    return image


def add_intermediate_image(
    db: Session, submission: HomeworkSubmission, *, url: str, path: str | None
) -> HomeworkSubmissionImage:
    order = count_intermediate_images(db, submission.id) + 1
    image = HomeworkSubmissionImage(
        submission_id=submission.id,
        image_s3_url=url,
        image_s3_path=path,
        is_final=False,
        sort_order=order,
    )
    db.add(image)
    db.flush()
    return image


def list_submissions_for_task(db: Session, tracker_task_id: int) -> list[HomeworkSubmission]:
    """Кто из учеников сдал эту постановку — для экрана куратора.

    Сортировка через `case`, не `.desc().nullslast()`: SQLite в тестах и
    Postgres на проде по-разному ставят NULL в убывающей сортировке (тот же
    повод, что у `list_tasks` в app/services/tracker.py).
    """
    no_date_last = case((HomeworkSubmission.submitted_at.is_(None), 1), else_=0)
    return (
        db.query(HomeworkSubmission)
        .filter(HomeworkSubmission.tracker_task_id == tracker_task_id)
        .order_by(no_date_last, HomeworkSubmission.submitted_at.desc())
        .all()
    )
