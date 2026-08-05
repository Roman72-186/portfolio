"""Queries and state transitions for the local learning-video catalogue."""

from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee
from app.models.learning_video import LearningVideo
from app.services.bunny_stream import is_bunny_stream_available
from app.services.mock_exam_access import (
    get_matching_target_tag_ids_for_student,
    is_mock_exam_ticket_submission_open,
)

# Ранг, с которого сотрудник видит все уроки независимо от тем (preview куратора).
STAFF_PREVIEW_RANK = 2


def list_all_videos(db: Session) -> list[LearningVideo]:
    return (
        db.query(LearningVideo)
        .filter(LearningVideo.deleted_at.is_(None))
        .order_by(LearningVideo.sort_order.asc(), LearningVideo.created_at.desc())
        .all()
    )


def accessible_assignment_ids(db: Session, user_id: int) -> set[int]:
    """Темы (ExamAssignment), открытые ученику прямо сейчас.

    Правило намеренно совпадает с выдачей билетов пробника (source of truth —
    app/services/mock_exam_access.py): тема открыта, если она опубликована и в
    ней есть билет, назначенный ученику тегом, флагом «всем» или персонально, у
    которого уже наступил opens_at. Одинаковое правило нужно, чтобы видео и
    задание одной темы открывались ученику вместе: разъедься они, ученик получит
    билет без урока или наоборот.

    Верхняя граница окна доступ не закрывает — как и для сдачи билета. Тема,
    неделя которой прошла, остаётся в каталоге: это учебный архив, а не экзамен.
    """
    assignee_ticket_ids = (
        db.query(ExamTicketAssignee.ticket_id)
        .filter(ExamTicketAssignee.user_id == user_id)
        .scalar_subquery()
    )
    matching_target_tag_ids = get_matching_target_tag_ids_for_student(db, user_id)
    tickets = (
        db.query(ExamTicket)
        .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
        .filter(
            ExamAssignment.status == "published",
            or_(
                ExamTicket.target_tag_id.in_(matching_target_tag_ids),
                and_(
                    ExamTicket.target_tag_id.is_(None),
                    or_(
                        ExamTicket.assign_to_all.is_(True),
                        ExamTicket.id.in_(assignee_ticket_ids),
                    ),
                ),
            ),
        )
        .all()
    )
    return {
        ticket.assignment_id
        for ticket in tickets
        if is_mock_exam_ticket_submission_open(ticket)
    }


def _is_staff_viewer(viewer: dict | None) -> bool:
    return bool(viewer) and viewer.get("role_rank", 0) >= STAFF_PREVIEW_RANK


def is_video_accessible(db: Session, video, viewer: dict) -> bool:
    """Открыт ли конкретный урок этому зрителю.

    Урок без темы (assignment_id IS NULL) открыт всем ученикам — так вели себя
    все ролики до появления тем, и молча закрывать их при выкатке нельзя.
    Привязанный урок открыт вместе со своей темой.
    """
    if _is_staff_viewer(viewer):
        return True
    assignment_id = getattr(video, "assignment_id", None)
    if assignment_id is None:
        return True
    return assignment_id in accessible_assignment_ids(db, viewer["user_id"])


def list_published_videos(db: Session, *, viewer: dict) -> list[LearningVideo]:
    if not settings.bunny_stream_enabled:
        return []
    videos = (
        db.query(LearningVideo)
        .filter(
            LearningVideo.deleted_at.is_(None),
            LearningVideo.is_published.is_(True),
            LearningVideo.status == "ready",
        )
        .order_by(LearningVideo.sort_order.asc(), LearningVideo.created_at.asc())
        .all()
    )
    if videos:
        if _is_staff_viewer(viewer):
            return videos
        # Один расчёт доступных тем на весь каталог, а не по ролику.
        allowed = accessible_assignment_ids(db, viewer["user_id"])
        return [
            video
            for video in videos
            if video.assignment_id is None or video.assignment_id in allowed
        ]
    if db.query(LearningVideo.id).first() or not is_bunny_stream_available():
        return []
    return [legacy_pilot_video()]


def legacy_pilot_video():
    """Temporary compatibility object until the deterministic pilot migration runs."""
    return SimpleNamespace(
        id=0,
        bunny_library_id=settings.bunny_stream_library_id,
        bunny_video_id=settings.bunny_stream_video_id,
        title=settings.bunny_stream_video_title,
        description=None,
        status="ready",
        is_published=True,
        duration_seconds=None,
        sort_order=0,
        deleted_at=None,
    )


def get_published_video(db: Session, video_id: int, *, viewer: dict):
    """Опубликованный урок, если он открыт этому зрителю.

    Отдельный от каталога путь: прямой заход по /cabinet/videos/{id} на чужую
    тему обязан упереться в ту же проверку, иначе ссылка обходит фильтр.
    """
    if not settings.bunny_stream_enabled:
        return None
    if video_id == 0 and not db.query(LearningVideo.id).first() and is_bunny_stream_available():
        return legacy_pilot_video()
    video = (
        db.query(LearningVideo)
        .filter(
            LearningVideo.id == video_id,
            LearningVideo.deleted_at.is_(None),
            LearningVideo.is_published.is_(True),
            LearningVideo.status == "ready",
        )
        .first()
    )
    if video is None or not is_video_accessible(db, video, viewer):
        return None
    return video


def publish_video(video: LearningVideo, *, user_id: int) -> None:
    if video.status != "ready" or video.deleted_at is not None:
        raise ValueError("Video is not ready for publication")
    video.is_published = True
    video.published_at = datetime.now(timezone.utc)
    video.published_by_id = user_id


def unpublish_video(video: LearningVideo) -> None:
    video.is_published = False
    video.published_at = None
    video.published_by_id = None
