"""Match holiday coursework and participants through the flow's existing tag."""

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.constants import PROGRAM_HOLIDAY, REPORT_EXCLUDED_USER_IDS
from app.models.intake_link import IntakeLink
from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic, LearningTopicTag
from app.models.role import Role
from app.models.tag import UserTag
from app.models.user import User

QUEUE_PATH = "/cabinet/staff/students-review/holiday"
ASSIGN_PATH = "/cabinet/holiday-curators"
HISTORY_PATH = "/cabinet/my-feedback"


def holiday_review_topic_ids():
    from app.services.video_topics import holiday_topic_clause

    parent = aliased(LearningTopic)
    grandparent = aliased(LearningTopic)
    return (
        select(LearningTopic.id)
        .outerjoin(parent, parent.id == LearningTopic.parent_id)
        .outerjoin(grandparent, grandparent.id == parent.parent_id)
        .where(
            holiday_topic_clause(),
            LearningTopic.deleted_at.is_(None),
            parent.deleted_at.is_(None),
            grandparent.deleted_at.is_(None),
        )
    )


def holiday_review_tracks(db: Session):
    """Текущие и будущие потоки, у которых есть цикл в АОП."""
    cycle_ids = holiday_review_topic_ids().where(LearningTopic.kind == TOPIC_KIND_WEEK)
    linked_cycle = select(LearningTopicTag.topic_id).where(
        LearningTopicTag.tag_id == IntakeLink.tag_id, LearningTopicTag.topic_id.in_(cycle_ids),
    ).exists()
    # Продажи могут закончиться раньше самой программы.
    return db.query(IntakeLink).filter(
        IntakeLink.program == PROGRAM_HOLIDAY,
        IntakeLink.deleted_at.is_(None),
        IntakeLink.access_until > datetime.now(timezone.utc),
        linked_cycle,
    ).order_by(IntakeLink.created_at, IntakeLink.id)


def active_holiday_students(db: Session, user: dict):
    query = db.query(User).join(Role, User.role_id == Role.id).filter(
        Role.rank == 1, User.program == PROGRAM_HOLIDAY, User.is_active.is_(True),
        User.deleted_at.is_(None), User.id.notin_(REPORT_EXCLUDED_USER_IDS),
    )
    if (user.get("nav_rank") or user["role_rank"]) < 4:
        query = query.filter(User.curator_id == user["user_id"])
    if user.get("viewer_tariff"):
        query = query.filter(User.tariff == user["viewer_tariff"])
    return query


def task_in_student_flow(topic_id, user_id, flow_id: int):
    return (
        select(IntakeLink.id)
        .select_from(IntakeLink)
        .join(LearningTopicTag, LearningTopicTag.tag_id == IntakeLink.tag_id)
        .join(UserTag, UserTag.tag_id == IntakeLink.tag_id)
        .where(
            IntakeLink.program == PROGRAM_HOLIDAY, IntakeLink.id == flow_id,
            LearningTopicTag.topic_id == topic_id, UserTag.user_id == user_id,
        )
        .exists()
    )


def topic_open_for_participant(topic_id, user_id: int, now: datetime, *, include_upcoming: bool = False):
    """A new purchase extends the account but each tagged program keeps its deadline."""
    tag = aliased(LearningTopicTag)
    track = aliased(IntakeLink)
    matches = select(track.id).join(tag, tag.tag_id == track.tag_id).where(
        track.program == PROGRAM_HOLIDAY, tag.topic_id == topic_id,
    )
    owned = matches.join(UserTag, UserTag.tag_id == track.tag_id).where(
        UserTag.user_id == user_id,
        or_(track.access_until.is_(None), track.access_until > now),
    )
    if not include_upcoming:
        owned = owned.where(or_(track.access_from.is_(None), track.access_from <= now))
    return or_(~matches.exists(), owned.exists())


def tracks_for_topics(
    db: Session, topic_ids: set[int], *, track_ids: set[int] | None = None,
) -> dict[int, list[IntakeLink]]:
    if not topic_ids:
        return {}
    pairs = (
        db.query(LearningTopicTag.topic_id, IntakeLink)
        .join(IntakeLink, IntakeLink.tag_id == LearningTopicTag.tag_id)
        .filter(LearningTopicTag.topic_id.in_(topic_ids), IntakeLink.program == PROGRAM_HOLIDAY)
        .order_by(IntakeLink.id)
    )
    if track_ids is not None:
        pairs = pairs.filter(IntakeLink.id.in_(track_ids))
    result = {}
    for topic_id, link in pairs:
        result.setdefault(topic_id, []).append(link)
    return result
