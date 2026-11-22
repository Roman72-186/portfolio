"""Access window for the annual course intake that started on November 22."""
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, aliased

from app.models.learning_topic import (
    TOPIC_KIND_STAGE,
    TOPIC_KIND_WEEK,
    LearningTopic,
    LearningTopicTag,
)
from app.models.tag import Tag, UserTag

NOVEMBER_INTAKE_TAG = "Адаптация · 22 ноября 2026"
DECEMBER_START = datetime(2026, 12, 1, tzinfo=ZoneInfo("Europe/Moscow"))


def november_cycle(db: Session, user_id: int) -> LearningTopic | None:
    return (
        db.query(LearningTopic)
        .join(LearningTopicTag, LearningTopicTag.topic_id == LearningTopic.id)
        .join(Tag, Tag.id == LearningTopicTag.tag_id)
        .join(UserTag, UserTag.tag_id == Tag.id)
        .filter(
            UserTag.user_id == user_id,
            Tag.name == NOVEMBER_INTAKE_TAG,
            LearningTopic.kind == TOPIC_KIND_WEEK,
            LearningTopic.ends_at.isnot(None),
        )
        .order_by(LearningTopic.id)
        .first()
    )


def first_december_cycle(db: Session) -> LearningTopic | None:
    stage = aliased(LearningTopic)
    return (
        db.query(LearningTopic)
        .join(stage, stage.id == LearningTopic.parent_id)
        .filter(
            stage.kind == TOPIC_KIND_STAGE,
            stage.title.ilike("Декабрь"),
            LearningTopic.title.ilike("1 неделя"),
            LearningTopic.kind == TOPIC_KIND_WEEK,
            LearningTopic.deleted_at.is_(None),
            LearningTopic.opens_at >= DECEMBER_START,
            LearningTopic.opens_at < DECEMBER_START.replace(year=2027, month=1),
        )
        .order_by(LearningTopic.opens_at, LearningTopic.id)
        .first()
    )
