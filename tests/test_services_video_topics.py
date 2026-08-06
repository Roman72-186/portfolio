from datetime import timedelta

from app.models.learning_topic import LearningTopic, LearningTopicAssignee, LearningTopicTag
from app.models.tag import Tag, UserTag
from app.services.tz import now_msk
from app.services.video_topics import (
    accessible_topic_ids,
    create_topic,
    delete_topic,
    get_assignee_ids,
    get_tag_ids,
    get_topic,
    list_topics,
    publish_topic,
    set_topic_assignees,
    set_topic_tags,
    unpublish_topic,
    update_topic,
)


def _topic(db, owner, *, assign_to_all=False, opens_in_days=-1, is_published=True,
           title="Архитектура США"):
    topic = LearningTopic(
        title=title,
        opens_at=now_msk() + timedelta(days=opens_in_days),
        assign_to_all=assign_to_all,
        is_published=is_published,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    return topic


def _tag(db, name, *, user=None):
    tag = Tag(name=name)
    db.add(tag)
    db.flush()
    if user is not None:
        db.add(UserTag(user_id=user.id, tag_id=tag.id))
    db.commit()
    return tag


# ---------------------------------------------------------------------------
# Кому открыта тема
# ---------------------------------------------------------------------------

def test_topic_for_everyone_is_open(db, regular_user, admin_user):
    topic = _topic(db, admin_user, assign_to_all=True)
    assert accessible_topic_ids(db, regular_user.id) == {topic.id}


def test_topic_matches_only_its_own_tags(db, regular_user, admin_user, user_factory):
    insider = user_factory(vk_id=300_001, name="Свой")
    tag = _tag(db, "Поток-1", user=insider)
    topic = _topic(db, admin_user)
    db.add(LearningTopicTag(topic_id=topic.id, tag_id=tag.id))
    db.commit()

    assert accessible_topic_ids(db, insider.id) == {topic.id}
    assert accessible_topic_ids(db, regular_user.id) == set()


def test_student_tag_unrelated_to_topic_does_not_open_it(db, regular_user, admin_user):
    _tag(db, "МАКСИМУМ", user=regular_user)
    topic = _topic(db, admin_user)
    other_tag = _tag(db, "УВЕРЕННЫЙ")
    db.add(LearningTopicTag(topic_id=topic.id, tag_id=other_tag.id))
    db.commit()

    assert accessible_topic_ids(db, regular_user.id) == set()


def test_tag_matching_is_strict_without_subject_heuristics(db, regular_user, admin_user):
    """Ученик с «Р+К» не получает тему, адресованную «Р».

    У пробников такой ученик билет получил бы: там работает эвристика предметов
    из mock_exam_access. Видеомодуль намеренно её не использует — в проде эти
    теги означают группу и уровень куратора, а не предмет.
    """
    _tag(db, "Р+К", user=regular_user)
    topic = _topic(db, admin_user)
    narrow = _tag(db, "Р")
    db.add(LearningTopicTag(topic_id=topic.id, tag_id=narrow.id))
    db.commit()

    assert accessible_topic_ids(db, regular_user.id) == set()


def test_personal_assignment_opens_topic_without_tags(db, regular_user, admin_user):
    topic = _topic(db, admin_user)
    db.add(LearningTopicAssignee(topic_id=topic.id, user_id=regular_user.id))
    db.commit()

    assert accessible_topic_ids(db, regular_user.id) == {topic.id}


def test_topic_before_opens_at_is_closed(db, regular_user, admin_user):
    _topic(db, admin_user, assign_to_all=True, opens_in_days=2)
    assert accessible_topic_ids(db, regular_user.id) == set()


def test_past_topic_stays_open_as_archive(db, regular_user, admin_user):
    topic = _topic(db, admin_user, assign_to_all=True, opens_in_days=-90)
    assert accessible_topic_ids(db, regular_user.id) == {topic.id}


def test_draft_and_deleted_topics_are_closed(db, regular_user, admin_user):
    _topic(db, admin_user, assign_to_all=True, is_published=False, title="Черновик")
    deleted = _topic(db, admin_user, assign_to_all=True, title="Удалённая")
    delete_topic(deleted)
    db.commit()

    assert accessible_topic_ids(db, regular_user.id) == set()


# ---------------------------------------------------------------------------
# Управление темами
# ---------------------------------------------------------------------------

def test_create_update_and_publication_cycle(db, admin_user):
    opens_at = now_msk() + timedelta(days=1)
    topic = create_topic(
        db, title="Тема", opens_at=opens_at, user_id=admin_user.id, assign_to_all=True
    )
    db.commit()
    assert topic.is_published is False

    publish_topic(topic, user_id=admin_user.id)
    db.commit()
    assert topic.is_published is True
    assert topic.published_at is not None
    assert topic.published_by_id == admin_user.id

    update_topic(topic, title="Тема недели", opens_at=opens_at, assign_to_all=False)
    db.commit()
    assert topic.title == "Тема недели"
    assert topic.assign_to_all is False

    unpublish_topic(topic)
    db.commit()
    assert topic.is_published is False
    assert topic.published_at is None


def test_setting_tags_and_assignees_replaces_previous(db, admin_user, user_factory):
    topic = _topic(db, admin_user)
    first = _tag(db, "Первый")
    second = _tag(db, "Второй")
    student = user_factory(vk_id=300_002, name="Ученик")

    set_topic_tags(db, topic, [first.id, second.id])
    set_topic_assignees(db, topic, [student.id])
    db.commit()
    assert sorted(get_tag_ids(db, topic.id)) == sorted([first.id, second.id])
    assert get_assignee_ids(db, topic.id) == [student.id]

    set_topic_tags(db, topic, [second.id])
    set_topic_assignees(db, topic, [])
    db.commit()
    assert get_tag_ids(db, topic.id) == [second.id]
    assert get_assignee_ids(db, topic.id) == []


def test_deleted_topic_disappears_from_listing_and_lookup(db, admin_user):
    topic = _topic(db, admin_user, assign_to_all=True)
    assert get_topic(db, topic.id) is topic

    delete_topic(topic)
    db.commit()

    assert get_topic(db, topic.id) is None
    assert list_topics(db) == []
    assert len(list_topics(db, include_deleted=True)) == 1


# ---------------------------------------------------------------------------
# Развязка с пробниками
# ---------------------------------------------------------------------------

def test_video_topic_never_appears_among_mock_exam_tickets(db, regular_user, admin_user):
    """Ради этого модуль и развязывали.

    Пока тема жила в ExamAssignment, она попадала в один пул с пробниками:
    get_active_tickets фильтрует задания по предмету и статусу, но не по типу,
    и урок мог выпасть ученику как вариант пробника.
    """
    from app.services.exam_cycle import get_active_tickets

    _topic(db, admin_user, assign_to_all=True, title="Архитектура США")
    db.commit()

    for subject in ("Рисунок", "Композиция"):
        assert get_active_tickets(db, regular_user.id, subject) == []
