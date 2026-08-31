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

from app.models.learning_topic import (
    TOPIC_KIND_WEEK,
    LearningTopic,
    LearningTopicAssignee,
    LearningTopicTag,
    LearningTopicTariff,
)
from app.models.role import Role
from app.models.tag import Tag, UserTag
from app.models.user import User
from app.services.tz import now_msk

STUDENT_ROLE_RANK = 1

# Однобуквенные «Р» и «К» (и их склейки вроде «Р+К») в проде означают группу и
# уровень куратора, а в модуле пробников такие же имена трактуются как маркеры
# предмета. Здесь сопоставление строгое: тема, адресованная тегу «Р», не дойдёт
# до учеников с тегом «Р+К» — главный преподаватель обычно ждёт обратного.
# Значение намеренно не импортируется из mock_exam_access: это подсказка ему, а не
# правило доступа,
# и связь видеомодуля с пробниками остаётся разорванной.
AMBIGUOUS_TAG_LETTERS = frozenset("рк")


def list_topics(
    db: Session,
    *,
    include_deleted: bool = False,
    kinds: tuple[str, ...] | None = (TOPIC_KIND_WEEK,),
) -> list[LearningTopic]:
    """Темы для списков и выпадающих меню.

    По умолчанию отдаются только темы недель: служебные темы элементов учебной
    программы человек не заводил и выбирать их ему незачем. `kinds=None` снимает
    фильтр целиком.
    """
    query = db.query(LearningTopic)
    if not include_deleted:
        query = query.filter(LearningTopic.deleted_at.is_(None))
    if kinds is not None:
        query = query.filter(LearningTopic.kind.in_(kinds))
    return query.order_by(
        LearningTopic.sort_order.asc(), LearningTopic.opens_at.desc()
    ).all()


def get_topic(
    db: Session, topic_id: int, *, kinds: tuple[str, ...] | None = None
) -> LearningTopic | None:
    """Тема по id. `kinds` сужает вид — например, чтобы экран недель не открылся
    на служебной теме элемента программы."""
    topic = db.get(LearningTopic, topic_id)
    if topic is None or topic.deleted_at is not None:
        return None
    if kinds is not None and topic.kind not in kinds:
        return None
    return topic


def accessible_topic_ids(db: Session, user_id: int) -> set[int]:
    """Темы, открытые ученику прямо сейчас.

    Тема открыта, если опубликована, наступил её `opens_at`, адресована
    ученику (флагом «всем», пересечением тегов или поимённо) и не скрыта по
    тарифу. Это ученический контракт функции: тарифный фильтр здесь
    сознательно не выключается никаким аргументом — куратор/staff читают
    элементы дня напрямую (`program.py::items_for_day`/`item_details`), в
    обход этой функции, и видят их независимо от тарифа (владелец 26.08.2026).

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
    # Тариф ученика — плоская строка, не EncryptedString, сравнивать в SQL
    # можно напрямую. Пустой/None тариф просто не совпадёт ни с одной строкой
    # LearningTopicTariff — тарифно-ограниченные темы остаются скрытыми.
    tariff = db.query(User.tariff).filter(User.id == user_id).scalar()
    tariff = (tariff or "").strip().upper()
    tariff_ok_topic_ids = (
        db.query(LearningTopicTariff.topic_id)
        .filter(LearningTopicTariff.tariff == tariff)
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
            or_(
                LearningTopic.tariff_restricted.is_(False),
                LearningTopic.id.in_(tariff_ok_topic_ids),
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
    kind: str = TOPIC_KIND_WEEK,
) -> LearningTopic:
    topic = LearningTopic(
        title=title,
        description=description,
        opens_at=opens_at,
        assign_to_all=assign_to_all,
        kind=kind,
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


def get_topic_tariffs(db: Session, topic_id: int) -> list[str]:
    rows = (
        db.query(LearningTopicTariff.tariff)
        .filter(LearningTopicTariff.topic_id == topic_id)
        .all()
    )
    return [row[0] for row in rows]


def set_topic_tariffs(
    db: Session, topic: LearningTopic, *, tariff_restricted: bool, tariffs: list[str]
) -> None:
    """Переписать тарифную видимость целиком — та же семантика, что у
    `set_topic_tags`. `tariff_restricted=False` чистит строки, а не оставляет
    их висеть неиспользуемыми: включили ограничение заново — список тарифов
    не должен внезапно вернуться из прошлого состояния."""
    topic.tariff_restricted = tariff_restricted
    db.query(LearningTopicTariff).filter(
        LearningTopicTariff.topic_id == topic.id
    ).delete(synchronize_session=False)
    if tariff_restricted:
        for tariff in dict.fromkeys(t.strip().upper() for t in tariffs if t.strip()):
            db.add(LearningTopicTariff(topic_id=topic.id, tariff=tariff))
    db.flush()


def ambiguous_tag_names(db: Session, tag_ids: list[int]) -> list[str]:
    """Имена выбранных тегов, которые легко понять не так.

    Возвращает теги вида «Р», «К», «Р+К» — см. AMBIGUOUS_TAG_LETTERS. Доступ они
    не меняют, но главный преподаватель, который ждёт «все, кто учит рисунок»,
    получит только
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
    tariff_restricted: bool = False,
    tariffs: list[str] | None = None,
) -> int:
    """Сколько активных учеников реально получат тему.

    Считается по той же адресации, что и в accessible_topic_ids, и служит
    проверкой на глаз: адресовал теме «Р» и увидел трёх человек вместо сорока —
    значит выбран не тот тег. `tariff_restricted`/`tariffs` сужают охват так же,
    как в accessible_topic_ids — иначе цифра на созданном с тарифным
    ограничением элементе будет враньём.

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
    if tariff_restricted:
        tariff_values = [t.strip().upper() for t in (tariffs or []) if t.strip()]
        # Список тарифов пуст — оператору IN нечего сопоставлять, а
        # `User.tariff.in_([])` на некоторых диалектах ведёт себя странно;
        # `in_(())` даёт заведомо ложное условие на любом бэкенде.
        students = students.filter(User.tariff.in_(tariff_values or ()))

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
