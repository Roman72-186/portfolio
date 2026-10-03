"""Кнопка блока «Ссылка» ведёт через сервер, адрес занятия ученику не уходит.

Владелец 17.08 и 03.10.2026: «чтобы не ссылка была у ребёнка, иначе он её
скопирует и перешлёт, а именно кнопка, в которой зашита ссылка». До 03.10.2026
в кнопке лежал сам адрес Zoom: долгое нажатие «Скопировать ссылку» — и он
открывал занятие тому, кто ничего не сдал. Теперь в кнопке адрес
`/cabinet/tracker/blocks/{id}/go`, сервер пускает дальше только того, кому шаг
открыт в ленте (`cycle_feed.block_step_is_open`).
"""
from datetime import timedelta

from app.models.task_block import BLOCK_LINK, BLOCK_UPLOAD, TaskBlock
from app.services.program import day_bounds
from app.services.task_blocks import close_block_for_user
from app.services.tracker import create_task
from app.services.tz import today_msk

ZOOM = "https://zoom.us/j/123456789?pwd=secret"


def _lesson(db, user, *, with_homework_before):
    """Задание на сегодня: (домашка обязательным шагом) → кнопка на занятие."""
    task = create_task(
        db, title="Занятие", user_id=user.id,
        due_at=day_bounds(today_msk())[1] - timedelta(minutes=1),
        assign_to_all=True, kind="homework", is_required=True,
    )
    task.is_published = True
    upload = None
    if with_homework_before:
        upload = TaskBlock(
            task_id=task.id, block_type=BLOCK_UPLOAD, body="Сдай работу",
            sort_order=0, is_required=True,
        )
        db.add(upload)
    link = TaskBlock(
        task_id=task.id, block_type=BLOCK_LINK, title="Подключиться к занятию",
        url=ZOOM, sort_order=1, is_required=False,
    )
    db.add(link)
    db.commit()
    return task, upload, link


def test_student_payload_has_no_meeting_address(auth_client, db):
    client, user = auth_client
    task, _, link = _lesson(db, user, with_homework_before=False)

    resp = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks")

    assert resp.status_code == 200, resp.text
    assert "zoom.us" not in resp.text
    [item] = [b for b in resp.json()["blocks"] if b["id"] == link.id]
    assert item["go_url"] == f"/cabinet/tracker/blocks/{link.id}/go"
    assert "url" not in item


def test_open_step_redirects_to_the_meeting(auth_client, db):
    client, user = auth_client
    _, _, link = _lesson(db, user, with_homework_before=False)

    resp = client.get(f"/cabinet/tracker/blocks/{link.id}/go", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == ZOOM
    assert resp.headers["cache-control"] == "no-store"


def test_step_behind_unsent_homework_does_not_open(auth_client, db):
    """Главное ради чего роут: кнопку переслали однокурснику, а он домашку не
    сдал — в Zoom его не пускает, пока шаг в его ленте закрыт."""
    client, user = auth_client
    _, upload, link = _lesson(db, user, with_homework_before=True)

    locked = client.get(f"/cabinet/tracker/blocks/{link.id}/go", follow_redirects=False)
    assert locked.status_code == 403
    assert "zoom.us" not in locked.text
    assert "Ссылка пока закрыта" in locked.text
    assert "Аккаунт заблокирован" not in locked.text

    close_block_for_user(db, upload, user.id, source="upload")
    db.commit()

    opened = client.get(f"/cabinet/tracker/blocks/{link.id}/go", follow_redirects=False)
    assert opened.status_code == 302
    assert opened.headers["location"] == ZOOM


def test_forwarded_button_without_login_goes_to_login(client, db, regular_user):
    _, _, link = _lesson(db, regular_user, with_homework_before=False)

    resp = client.get(f"/cabinet/tracker/blocks/{link.id}/go", follow_redirects=False)

    assert resp.status_code == 302
    assert "zoom.us" not in resp.headers["location"]


def test_go_is_only_for_link_blocks(auth_client, db):
    client, user = auth_client
    _, upload, _ = _lesson(db, user, with_homework_before=True)

    resp = client.get(f"/cabinet/tracker/blocks/{upload.id}/go", follow_redirects=False)

    assert resp.status_code == 404
