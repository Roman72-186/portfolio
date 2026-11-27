from datetime import datetime, timedelta, timezone
from html import unescape
import re
from urllib.parse import parse_qs, urlsplit

from app.constants import TARIFF_CONFIDENT_MAX, TARIFF_WITH_YOU
from app.models.learning_topic import LearningTopic
from app.models.task_block import BLOCK_UPLOAD, TaskBlock, TaskBlockFeedbackLink, TaskBlockSubmission
from app.models.tracker import ITEM_MOCK_EXAM
from app.services.tracker import create_task


QUEUE = "/cabinet/staff/students-review/queue"
SUBMISSIONS = "/cabinet/staff/task-block-submissions"


def test_navigation_keeps_queue_filters_after_review(client, db, user_factory, session_factory):
    now = datetime.now(timezone.utc)
    curator = user_factory(vk_id=70201, role_name="куратор")
    another = user_factory(vk_id=70202, role_name="куратор")
    cycle = LearningTopic(
        title="Неделя для навигации", opens_at=now - timedelta(days=2), ends_at=now + timedelta(days=2),
        assign_to_all=True, is_published=True,
    )
    db.add(cycle)
    db.flush()
    task = create_task(db, user_id=curator.id, title="Практика", topic_id=cycle.id, subject="Рисунок", kind=ITEM_MOCK_EXAM)
    task.is_published = True
    block = TaskBlock(task_id=task.id, block_type=BLOCK_UPLOAD, title="Работа", is_required=True)
    db.add(block)
    db.flush()

    def submission(vk_id, owner, tariff, minutes, reviewed=False):
        pupil = user_factory(vk_id=vk_id, name=f"Ученик {vk_id}", tariff=tariff)
        pupil.curator_id = owner.id
        item = TaskBlockSubmission(
            user_id=pupil.id, block_id=block.id, submitted_at=now - timedelta(minutes=minutes),
            reviewed_at=now if reviewed else None,
        )
        db.add(item)
        db.flush()
        return item

    first = submission(70203, curator, TARIFF_CONFIDENT_MAX, 1)
    reviewed = submission(70204, curator, TARIFF_CONFIDENT_MAX, 2, reviewed=True)
    second = submission(70205, curator, TARIFF_CONFIDENT_MAX, 3)
    foreign = submission(70206, another, TARIFF_CONFIDENT_MAX, 4)
    different_tariff = submission(70207, curator, TARIFF_WITH_YOU, 5)
    linked_block = TaskBlock(task_id=task.id, block_type=BLOCK_UPLOAD, title="Связанная работа")
    db.add(linked_block)
    db.flush()
    linked = TaskBlockSubmission(user_id=first.user_id, block_id=linked_block.id, submitted_at=now)
    db.add_all([linked, TaskBlockFeedbackLink(block_id=block.id, linked_block_id=linked_block.id)])
    db.commit()
    chief = user_factory(role_name="админ")
    client.cookies.set("session_id", session_factory(chief).id)

    def url_of(body, item):
        match = re.search(fr'href="({SUBMISSIONS}/{item.id}/feedback\?[^\"]+)"', body)
        assert match
        return unescape(match[1])

    params = {"subject": "Рисунок", "tariff": TARIFF_CONFIDENT_MAX, "cycle_id": cycle.id, "review_curator": curator.id}
    queue = client.get(QUEUE, params=params)
    assert queue.status_code == 200
    start_url = url_of(queue.text, first)
    second_url = url_of(queue.text, second)
    assert all(f"/{item.id}/feedback" not in queue.text for item in (reviewed, foreign, different_tariff, linked))

    linked_url = start_url.replace(f"/{first.id}/feedback", f"/{linked.id}/feedback")
    redirect = client.get(linked_url, follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"] == start_url

    page = client.get(start_url)
    assert page.status_code == 200
    assert 'rel="prev"' not in page.text
    assert f'rel="next" href="{second_url.replace("&", "&amp;")}"' in page.text
    assert parse_qs(urlsplit(second_url).query)["review_cycle_id"] == [str(cycle.id)]

    # После отметки о проверке текущая карточка исчезает из списка, но
    # переход вперёд с открытого экрана остаётся доступен.
    first.reviewed_at = now
    db.commit()
    assert 'rel="next"' in client.get(start_url).text
    last = client.get(second_url)
    assert 'rel="next"' not in last.text
    assert 'rel="prev"' not in last.text

    all_queue = client.get(QUEUE, params={**params, "status": "all"})
    all_url = url_of(all_queue.text, first)
    reviewed_url = url_of(all_queue.text, reviewed)
    all_first = client.get(all_url)
    assert f'rel="next" href="{reviewed_url.replace("&", "&amp;")}"' in all_first.text
    assert 'review_status=all' in reviewed_url
