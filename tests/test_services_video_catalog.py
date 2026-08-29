from datetime import timedelta

from app.models.learning_topic import LearningTopic, LearningTopicAssignee, LearningTopicTag
from app.models.learning_video import LearningVideo
from app.models.tag import Tag, UserTag
from app.services.tz import now_msk
from app.services.video_catalog import (
    get_published_video,
    list_published_videos,
    publish_video,
    sync_status_from_bunny,
    unpublish_video,
)


def _video(**overrides):
    values = {
        "bunny_library_id": 720058,
        "bunny_video_id": "35ed80ae-8103-4528-a700-3f69ec56957d",
        "title": "Урок",
        "status": "ready",
        "is_published": False,
    }
    values.update(overrides)
    return LearningVideo(**values)


def _viewer(user, rank: int = 1) -> dict:
    return {"user_id": user.id, "role_rank": rank, "is_group_member": True}


def _topic(db, owner, *, tag_id=None, assign_to_all=False, opens_in_days=-1,
           is_published=True, title="Архитектура США"):
    """Тема недели видеомодуля. С пробниками не связана."""
    topic = LearningTopic(
        title=title,
        opens_at=now_msk() + timedelta(days=opens_in_days),
        assign_to_all=assign_to_all,
        is_published=is_published,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.flush()
    if tag_id is not None:
        db.add(LearningTopicTag(topic_id=topic.id, tag_id=tag_id))
    db.commit()
    return topic


def _tag(db, user, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.flush()
    db.add(UserTag(user_id=user.id, tag_id=tag.id))
    db.commit()
    return tag


def test_catalogue_exposes_only_ready_published_videos(db, monkeypatch, regular_user):
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: False)
    published = _video(is_published=True, sort_order=2)
    processing = _video(
        bunny_video_id="a9a2f23a-3dd6-4f93-b74e-31dd47e21fe8",
        status="processing",
        is_published=True,
        sort_order=1,
    )
    hidden = _video(
        bunny_video_id="db60eaef-6898-4ec3-bf35-5f2d151239b7",
        is_published=False,
        sort_order=0,
    )
    db.add_all([published, processing, hidden])
    db.commit()

    assert list_published_videos(db, viewer=_viewer(regular_user)) == [published]


def test_catalogue_does_not_resurrect_legacy_pilot_after_unpublish(db, monkeypatch, regular_user):
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: True)
    hidden = _video(is_published=False)
    db.add(hidden)
    db.commit()

    assert list_published_videos(db, viewer=_viewer(regular_user)) == []


def test_publish_requires_ready_and_unpublish_clears_publication(db, regular_user):
    video = _video(status="processing")
    db.add(video)
    db.commit()

    try:
        publish_video(video, user_id=regular_user.id)
        assert False, "processing video must not publish"
    except ValueError:
        pass

    video.status = "ready"
    publish_video(video, user_id=regular_user.id)
    assert video.is_published is True
    assert video.published_at is not None
    assert video.published_by_id == regular_user.id

    unpublish_video(video)
    assert video.is_published is False
    assert video.published_at is None


# ---------------------------------------------------------------------------
# sync_status_from_bunny — автопубликация по факту готовности (29.08.2026)
# ---------------------------------------------------------------------------

def _stub_remote(monkeypatch, **fields):
    payload = {"status": 4, "encodeProgress": 100, "length": 120.0, "transcodingMessages": []}
    payload.update(fields)
    monkeypatch.setattr("app.services.video_catalog.get_video", lambda video_id: payload)
    return payload


def test_sync_publishes_video_flagged_for_auto_publish_on_ready(db, regular_user, monkeypatch):
    video = _video(status="processing", is_published=False)
    video.auto_publish_on_ready = True
    video.created_by_id = regular_user.id
    db.add(video)
    db.commit()
    _stub_remote(monkeypatch)

    became_published = sync_status_from_bunny(db, video)

    assert became_published is True
    assert video.status == "ready"
    assert video.is_published is True
    assert video.published_by_id == regular_user.id


def test_sync_does_not_publish_without_the_flag(db, monkeypatch):
    video = _video(status="processing", is_published=False)
    db.add(video)
    db.commit()
    _stub_remote(monkeypatch)

    became_published = sync_status_from_bunny(db, video)

    assert became_published is False
    assert video.status == "ready"
    assert video.is_published is False


def test_sync_does_not_republish_already_ready_video(db, monkeypatch):
    """Флаг срабатывает только в момент перехода в ready — иначе повторный
    sync уже готового ролика откатил бы ручную «Отменить публикацию»."""
    video = _video(status="ready", is_published=False)
    video.auto_publish_on_ready = True
    db.add(video)
    db.commit()
    _stub_remote(monkeypatch)

    became_published = sync_status_from_bunny(db, video)

    assert became_published is False
    assert video.is_published is False


def test_sync_updates_progress_and_duration_from_remote(db, monkeypatch):
    video = _video(status="processing")
    db.add(video)
    db.commit()
    _stub_remote(monkeypatch, status=2, encodeProgress=42, length=5376.0)

    sync_status_from_bunny(db, video)

    assert video.status == "processing"
    assert video.encode_progress == 42
    assert video.duration_seconds == 5376.0


# ---------------------------------------------------------------------------
# Доступ по темам недели (ExamAssignment)
# ---------------------------------------------------------------------------

def test_video_without_topic_stays_open_to_every_student(db, monkeypatch, regular_user):
    """Ролики, залитые до появления тем, не должны молча закрыться."""
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: False)
    video = _video(is_published=True)
    db.add(video)
    db.commit()

    assert list_published_videos(db, viewer=_viewer(regular_user)) == [video]


def test_topic_assigned_to_all_opens_its_video(db, monkeypatch, regular_user, admin_user):
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: False)
    topic = _topic(db, admin_user, assign_to_all=True)
    video = _video(is_published=True, topic_id=topic.id)
    db.add(video)
    db.commit()

    assert list_published_videos(db, viewer=_viewer(regular_user)) == [video]


def test_topic_targeted_at_a_tag_hides_video_from_other_students(
    db, monkeypatch, regular_user, admin_user, user_factory
):
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: False)
    insider = user_factory(vk_id=200_101, name="Свой поток")
    outsider = regular_user
    tag = _tag(db, insider, "Поток-1")
    topic = _topic(db, admin_user, tag_id=tag.id)
    video = _video(is_published=True, topic_id=topic.id)
    db.add(video)
    db.commit()

    assert list_published_videos(db, viewer=_viewer(insider)) == [video]
    assert list_published_videos(db, viewer=_viewer(outsider)) == []


def test_topic_that_has_not_started_yet_stays_closed(db, monkeypatch, regular_user, admin_user):
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: False)
    topic = _topic(db, admin_user, assign_to_all=True, opens_in_days=3)
    video = _video(is_published=True, topic_id=topic.id)
    db.add(video)
    db.commit()

    assert list_published_videos(db, viewer=_viewer(regular_user)) == []


def test_draft_topic_does_not_open_its_video(db, monkeypatch, regular_user, admin_user):
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: False)
    topic = _topic(db, admin_user, assign_to_all=True, is_published=False)
    video = _video(is_published=True, topic_id=topic.id)
    db.add(video)
    db.commit()

    assert list_published_videos(db, viewer=_viewer(regular_user)) == []


def test_personally_assigned_student_gets_the_topic(db, monkeypatch, regular_user, admin_user):
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: False)
    topic = _topic(db, admin_user, assign_to_all=False)
    db.add(LearningTopicAssignee(topic_id=topic.id, user_id=regular_user.id))
    video = _video(is_published=True, topic_id=topic.id)
    db.add(video)
    db.commit()

    assert list_published_videos(db, viewer=_viewer(regular_user)) == [video]


def test_staff_preview_sees_every_topic(db, monkeypatch, regular_user, admin_user, user_factory):
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: False)
    curator = user_factory(vk_id=200_102, name="Куратор", role_name="куратор")
    topic = _topic(db, admin_user, assign_to_all=False)
    video = _video(is_published=True, topic_id=topic.id)
    db.add(video)
    db.commit()

    assert list_published_videos(db, viewer=_viewer(curator, rank=2)) == [video]
    assert list_published_videos(db, viewer=_viewer(regular_user)) == []


def test_direct_link_to_foreign_topic_is_closed(db, monkeypatch, regular_user, admin_user):
    """Карточка урока — отдельный от каталога путь, ссылка не должна его обходить."""
    monkeypatch.setattr("app.services.video_catalog.is_bunny_stream_available", lambda: False)
    topic = _topic(db, admin_user, assign_to_all=False)
    video = _video(is_published=True, topic_id=topic.id)
    db.add(video)
    db.commit()

    assert get_published_video(db, video.id, viewer=_viewer(regular_user)) is None
    assert get_published_video(db, video.id, viewer=_viewer(admin_user, rank=4)) is video
