"""Read-only страница дня ученика (/cabinet/learning/day/{iso}).

Мирроринг cabinet_program_day.html (staff), но без форм создания — только
список задач дня с умной кнопкой по kind. Видимость — тот же движок
accessible_task_entries, что у /cabinet/tracker и /cabinet/learning.
"""

from datetime import timedelta

from app.models.learning_video import LearningVideo
from app.models.tag import Tag
from app.services.program import day_bounds, ensure_item_topic, set_item_audience
from app.services.tracker import create_task
from app.services.tz import today_msk

PAGE = "/cabinet/learning/day"


def _tag(db, name: str) -> Tag:
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def _day_and_due(hour: int = 10):
    """Момент внутри московских суток `day` (границы UTC, как в program.py)."""
    day = today_msk()
    start, _ = day_bounds(day)
    return day, start + timedelta(hours=hour)


def test_without_auth_redirects(client):
    resp = client.get(f"{PAGE}/2026-08-21", follow_redirects=False)
    assert resp.status_code == 302
    assert "session_expired" in resp.headers["location"]


def test_garbage_iso_is_404(auth_client):
    client, _ = auth_client
    resp = client.get(f"{PAGE}/not-a-date")
    assert resp.status_code == 404


def test_empty_day_renders_without_tasks(auth_client):
    client, user = auth_client
    day, _ = _day_and_due()
    resp = client.get(f"{PAGE}/{day.isoformat()}")
    assert resp.status_code == 200
    assert "пока ничего нет" in resp.text


def test_task_hidden_without_matching_tag(auth_client, db):
    client, user = auth_client
    tag = _tag(db, "Поток 1")
    day, due = _day_and_due()
    topic = ensure_item_topic(db, title="Видео дня", day=day, user_id=user.id)
    set_item_audience(db, topic, assign_to_all=False, tag_ids=[tag.id], assignee_ids=[])
    task = create_task(
        db, title="Видео дня", user_id=user.id, due_at=due, topic_id=topic.id, kind="video",
    )
    task.is_published = True
    db.commit()

    resp = client.get(f"{PAGE}/{day.isoformat()}")
    assert resp.status_code == 200
    assert "Видео дня" not in resp.text


def test_mock_exam_links_to_upload_with_subject(auth_client, db):
    client, user = auth_client
    day, due = _day_and_due()
    task = create_task(
        db,
        title="Пробник по предмету «Рисунок»",
        user_id=user.id,
        due_at=due,
        kind="mock_exam",
        subject="Рисунок",
        assign_to_all=True,
    )
    task.is_published = True
    db.commit()

    resp = client.get(f"{PAGE}/{day.isoformat()}")
    assert resp.status_code == 200
    assert "Пробник по предмету" in resp.text
    assert "/upload/mock-exam?subject=%D0%A0%D0%B8%D1%81%D1%83%D0%BD%D0%BE%D0%BA" in resp.text


def test_video_links_to_player_by_topic_id(auth_client, db):
    client, user = auth_client
    day, due = _day_and_due()
    topic = ensure_item_topic(db, title="Видео дня", day=day, user_id=user.id)
    set_item_audience(db, topic, assign_to_all=True, tag_ids=[], assignee_ids=[])
    task = create_task(
        db, title="Видео дня", user_id=user.id, due_at=due, topic_id=topic.id, kind="video",
    )
    task.is_published = True
    video = LearningVideo(
        bunny_library_id=1,
        bunny_video_id="abc-123",
        title="Видео дня",
        topic_id=topic.id,
        status="ready",
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    resp = client.get(f"{PAGE}/{day.isoformat()}")
    assert resp.status_code == 200
    assert f"/cabinet/videos/{video.id}" in resp.text


def test_homework_links_to_submission_page_not_toggle(auth_client, db):
    client, user = auth_client
    day, due = _day_and_due()
    task = create_task(
        db, title="Самостоятельная", user_id=user.id, due_at=due, kind="homework", assign_to_all=True,
    )
    task.is_published = True
    db.commit()

    resp = client.get(f"{PAGE}/{day.isoformat()}")
    assert resp.status_code == 200
    assert f"/cabinet/homework/{task.id}" in resp.text
    assert f'data-toggle-task="{task.id}"' not in resp.text


def test_other_kind_shows_toggle_button(auth_client, db):
    client, user = auth_client
    day, due = _day_and_due()
    task = create_task(
        db, title="Анкета дня", user_id=user.id, due_at=due, assign_to_all=True,
    )
    task.is_published = True
    db.commit()

    resp = client.get(f"{PAGE}/{day.isoformat()}")
    assert resp.status_code == 200
    assert f'data-toggle-task="{task.id}"' in resp.text
