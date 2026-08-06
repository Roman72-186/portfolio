"""Темы недели видеомодуля: доступ учеников и управление из админки.

Source of truth по тому, какие темы открыты ученику. С пробниками не связано
намеренно — `mock_exam_access` здесь не используется, сопоставление тегов строгое
по `tag_id`. Предметная эвристика пробников (однобуквенные «Р»/«К» как маркеры
предмета) на видеоуроки не распространяется: в проде эти теги означают группу и
уровень куратора, и на билетах она уже прятала задания от учеников.
"""

from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.learning_topic import LearningTopic, LearningTopicAssignee, LearningTopicTag
from app.models.role import Role
from app.models.tag import Tag, UserTag
from app.models.user import User
from app.services.tz import now_msk

STUDENT_ROLE_RANK = 1

# Однобуквенные «Р» и «К» (и их склейки вроде «Р+К») в проде означают группу и
# уровень куратора, а в модуле пробников такие же имена трактуются как маркеры
# предмета. Здесь сопоставление строгое: тема, адресованная тегу «Р», не дойдёт
# до учеников с тегом «Р+К» — админ обычно ждёт обратного. Значение намеренно не
# импортируется из mock_exam_access: это подсказка админу, а не правило доступа,
# и связь видеомодуля с пробниками остаётся разорванной.
AMBIGUOUS_TAG_LETTERS = frozenset("рк")


def list_topics(db: Session, *, include_deleted: bool = False) -> list[LearningTopic]:
    query = db.query(LearningTopic)
    if not include_deleted:
        query = query.filter(LearningTopic.deleted_at.is_(None))
    return query.order_by(
        LearningTopic.sort_order.asc(), LearningTopic.opens_at.desc()
    ).all()


def get_topic(db: Session, topic_id: int) -> LearningTopic | None:
    topic = db.get(LearningTopic, topic_id)
    if topic is None or topic.deleted_at is not None:
        return None
    return topic


def accessible_topic_ids(db: Session, user_id: int) -> set[int]:
    """Темы, открытые ученику прямо сейчас.

    Тема открыта, если опубликована, наступил её `opens_at` и она адресована
    ученику: флагом «всем», пересечением тегов или поимённо.

    Верхней границы у окна нет: прошедшая тема остаётся в каталоге как учебный
    архив. Время берём через `tz.now_msk()` — в контейнере UTC, иначе фильтр
    уезжает на три часа.
    """
    user_tag_ids = (
        db.query(UserTag.tag_id).filter(UserTag.user_id == user_id).scalar_subquery()
    )
    tagged_topic_ids = (
        db.query(LearningTopicTag.topic_id)
        .filter(LearningTopicTag.tag_id.in_(user_tag_ids))
        .scalar_subquery()
    )
    assigned_topic_ids = (
        db.query(LearningTopicAssignee.topic_id)
        .filter(LearningTopicAssignee.user_id == user_id)
        .scalar_subquery()
    )
    rows = (
        db.query(LearningTopic.id)
        .filter(
            LearningTopic.deleted_at.is_(None),
            LearningTopic.is_published.is_(True),
            LearningTopic.opens_at <= now_msk(),
            or_(
                LearningTopic.assign_to_all.is_(True),
                LearningTopic.id.in_(tagged_topic_ids),
                LearningTopic.id.in_(assigned_topic_ids),
            ),
        )
        .all()
    )
    return {row[0] for row in rows}


def create_topic(
    db: Session,
    *,
    title: str,
    opens_at: datetime,
    user_id: int,
    description: str | None = None,
    assign_to_all: bool = False,
) -> LearningTopic:
    topic = LearningTopic(
        title=title,
        description=description,
        opens_at=opens_at,
        assign_to_all=assign_to_all,
        created_by_id=user_id,
    )
    db.add(topic)
    db.flush()
    return topic


def update_topic(
    topic: LearningTopic,
    *,
    title: str,
    opens_at: datetime,
    description: str | None = None,
    assign_to_all: bool = False,
    sort_order: int | None = None,
) -> None:
    topic.title = title
    topic.description = description
    topic.opens_at = opens_at
    topic.assign_to_all = assign_to_all
    if sort_order is not None:
        topic.sort_order = sort_order


def set_topic_tags(db: Session, topic: LearningTopic, tag_ids: list[int]) -> None:
    """Переписать адресацию по тегам целиком."""
    db.query(LearningTopicTag).filter(LearningTopicTag.topic_id == topic.id).delete(
        synchronize_session=False
    )
    for tag_id in dict.fromkeys(tag_ids):
        db.add(LearningTopicTag(topic_id=topic.id, tag_id=tag_id))
    db.flush()


def set_topic_assignees(db: Session, topic: LearningTopic, user_ids: list[int]) -> None:
    """Переписать поимённые исключения целиком."""
    db.query(LearningTopicAssignee).filter(
        LearningTopicAssignee.topic_id == topic.id
    ).delete(synchronize_session=False)
    for user_id in dict.fromkeys(user_ids):
        db.add(LearningTopicAssignee(topic_id=topic.id, user_id=user_id))
    db.flush()


def get_tag_ids(db: Session, topic_id: int) -> list[int]:
    rows = (
        db.query(LearningTopicTag.tag_id)
        .filter(LearningTopicTag.topic_id == topic_id)
        .all()
    )
    return [row[0] for row in rows]


def get_assignee_ids(db: Session, topic_id: int) -> list[int]:
    rows = (
        db.query(LearningTopicAssignee.user_id)
        .filter(LearningTopicAssignee.topic_id == topic_id)
        .all()
    )
    return [row[0] for row in rows]


def ambiguous_tag_names(db: Session, tag_ids: list[int]) -> list[str]:
    """Имена выбранных тегов, которые легко понять не так.

    Возвращает теги вида «Р», «К», «Р+К» — см. AMBIGUOUS_TAG_LETTERS. Доступ они
    не меняют, но админ, который ждёт «все, кто учит рисунок», получит только
    точное совпадение по тегу.
    """
    if not tag_ids:
        return []
    rows = db.query(Tag.name).filter(Tag.id.in_(tag_ids)).all()
    names = []
    for (name,) in rows:
        compact = (name or "").strip().lower()
        for separator in (" ", "+", "/", ",", "-"):
            compact = compact.replace(separator, "")
        if compact and set(compact) <= AMBIGUOUS_TAG_LETTERS:
            names.append(name)
    return names


def count_topic_audience(
    db: Session,
    *,
    assign_to_all: bool,
    tag_ids: list[int],
    assignee_ids: list[int],
) -> int:
    """Сколько активных учеников реально получат тему.

    Считается по той же адресации, что и в accessible_topic_ids, и служит
    проверкой на глаз: адресовал теме «Р» и увидел трёх человек вместо сорока —
    значит выбран не тот тег.

    Учитывается и членство в группе: без него `require_learning_content_access`
    отдаёт ученику 403 на весь видеомодуль, и такой человек в охвате — обман.
    """
    students = (
        db.query(User.id)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == STUDENT_ROLE_RANK,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.is_group_member.is_(True),
        )
    )
    if assign_to_all:
        return students.count()

    reached: set[int] = set()
    if tag_ids:
        rows = (
            students.join(UserTag, UserTag.user_id == User.id)
            .filter(UserTag.tag_id.in_(tag_ids))
            .all()
        )
        reached.update(row[0] for row in rows)
    if assignee_ids:
        rows = students.filter(User.id.in_(assignee_ids)).all()
        reached.update(row[0] for row in rows)
    return len(reached)


def publish_topic(topic: LearningTopic, *, user_id: int) -> None:
    if topic.deleted_at is not None:
        raise ValueError("Topic is deleted")
    topic.is_published = True
    topic.published_at = datetime.now(timezone.utc)
    topic.published_by_id = user_id


def unpublish_topic(topic: LearningTopic) -> None:
    topic.is_published = False
    topic.published_at = None
    topic.published_by_id = None


def delete_topic(topic: LearningTopic) -> None:
    """Мягкое удаление. Уроки темы остаются, их topic_id обнулит FK ON DELETE
    только при физическом удалении — здесь связь сохраняется, но тема пропадает
    из выдачи, потому что accessible_topic_ids фильтрует по deleted_at."""
    topic.deleted_at = datetime.now(timezone.utc)
    topic.is_published = False
