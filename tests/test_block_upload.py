"""Приём работ прямо в задании — блок «Загрузить работы» (владелец 07.09.2026).

«Работы нужно загружать в заданиях.» До этой стройки блоки «Загрузить
портфолио» и «Работа на время» только уводили ученика ссылкой на общий экран
`/upload`: файл падал в портфолио, к заданию не привязывался, а блок
закрывался фактом любой новой работы за период цикла.

Теперь файлы принимает сам блок, работа привязана к нему (`TaskBlockSubmission`)
и видна куратору на едином экране проверки по ученику.
"""
from datetime import timedelta, timezone
from unittest.mock import patch

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import (
    BLOCK_TIMED,
    BLOCK_UPLOAD,
    MAX_SUBMISSION_IMAGES,
    TaskBlock,
    TaskBlockSubmission,
    TaskBlockSubmissionImage,
)
from app.services import s3 as s3_service
from app.services.cycle_feed import build_cycle_feed
from app.services.program import day_bounds
from app.services.review_aggregate import DOMAIN_BLOCK_WORK, student_review_items
from app.services.task_blocks import (
    get_state,
    get_submission,
    start_timed_block,
    sync_blocks,
)
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

FAKE_URL = "https://s3.example.com/zadaniya/work.jpg"

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=1)
CYCLE_END = TODAY + timedelta(days=6)
TODAY_TS = day_bounds(TODAY)[0]


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    topic = LearningTopic(
        title="Цикл со сдачей", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    return topic


def _task(db, owner, *, title="Задание со сдачей"):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _upload_block(db, task, *, order=1, block_type=BLOCK_UPLOAD):
    block = TaskBlock(
        task_id=task.id, block_type=block_type, title="Пришлите работу",
        sort_order=order, is_required=True,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _post(client, block_id, files=None, comment=""):
    files = files or [("photos", ("work.jpg", b"fake-bytes", "image/jpeg"))]
    with patch.object(s3_service, "upload_to_s3", return_value=FAKE_URL):
        return client.post(
            f"/cabinet/tracker/blocks/{block_id}/upload",
            files=files,
            data={"comment": comment},
        )


# ── приём файлов ────────────────────────────────────────────────────────────

def test_work_is_stored_against_the_block(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)

    resp = _post(client, block.id, comment="Три листа, карандаш")

    assert resp.status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    assert submission is not None
    assert submission.comment == "Три листа, карандаш"
    assert submission.submitted_at is not None
    images = (
        db.query(TaskBlockSubmissionImage)
        .filter(TaskBlockSubmissionImage.submission_id == submission.id)
        .all()
    )
    assert [i.image_s3_url for i in images] == [FAKE_URL]


def test_upload_closes_the_block(auth_client, db):
    """Шаг закрывается в момент загрузки, а не проверкой куратора: иначе
    ученик стоял бы в ленте, пока куратор не дойдёт до его работы."""
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)

    _post(client, block.id)

    state = get_state(db, block_id=block.id, user_id=user.id)
    assert state is not None and state.status == "done"


def test_second_upload_adds_a_file_and_keeps_one_submission(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)

    _post(client, block.id)
    _post(client, block.id)

    assert db.query(TaskBlockSubmission).count() == 1
    assert db.query(TaskBlockSubmissionImage).count() == 2


def test_limit_of_files_is_enforced(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    submission = TaskBlockSubmission(block_id=block.id, user_id=user.id)
    db.add(submission)
    db.commit()
    for index in range(MAX_SUBMISSION_IMAGES):
        db.add(TaskBlockSubmissionImage(
            submission_id=submission.id, image_s3_url=FAKE_URL, sort_order=index,
        ))
    db.commit()

    resp = _post(client, block.id)

    assert resp.status_code == 422
    assert db.query(TaskBlockSubmissionImage).count() == MAX_SUBMISSION_IMAGES


def test_upload_to_a_text_block_is_404(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = TaskBlock(task_id=task.id, block_type="text", body="текст", sort_order=1)
    db.add(block)
    db.commit()

    assert _post(client, block.id).status_code == 404


def test_upload_to_someone_elses_task_is_404(auth_client, db, admin_user):
    """Чужое задание не должно принимать работу — та же проверка доступа,
    что у остальных роутов ленты."""
    client, _ = auth_client
    task = create_task(
        db, title="Чужое", user_id=admin_user.id, kind="material",
        due_at=day_bounds(TODAY)[0] + timedelta(hours=6),
        assign_to_all=False, is_required=True,
    )
    task.is_published = True
    db.commit()
    block = _upload_block(db, task)

    assert _post(client, block.id).status_code == 404


# ── работа на время сдаётся тем же путём ────────────────────────────────────

def test_timed_block_accepts_the_work_in_place(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task, block_type=BLOCK_TIMED)
    start_timed_block(db, block=block, user_id=user.id)
    db.commit()

    resp = _post(client, block.id)

    assert resp.status_code == 200
    assert get_state(db, block_id=block.id, user_id=user.id).status == "done"


# ── лента ───────────────────────────────────────────────────────────────────

def test_feed_opens_the_tail_after_the_work_is_sent(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _upload_block(db, task)
    second = TaskBlock(
        task_id=task.id, block_type="text", body="Следующий шаг", sort_order=2,
    )
    db.add(second)
    db.commit()

    steps = build_cycle_feed(
        db, user_id=user.id, user_tariff=user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )
    assert [s["status"] for s in steps] == ["current", "locked"]

    _post(client, block.id)

    steps = build_cycle_feed(
        db, user_id=user.id, user_tariff=user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )
    assert [s["status"] for s in steps] == ["done", "current"]


def test_block_payload_carries_the_upload_endpoint(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()

    item = payload["blocks"][0]
    assert item["upload_endpoint"] == f"/cabinet/tracker/blocks/{block.id}/upload"
    assert item["max_files"] == MAX_SUBMISSION_IMAGES
    assert item["submitted_files"] == []


# ── проверка куратором ──────────────────────────────────────────────────────

def test_sent_work_lands_on_the_student_review_screen(auth_client, db, admin_user):
    """Инвариант проекта: новый тип сдачи получает адаптер в агрегаторе, а не
    свой роут и пункт меню."""
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _post(client, block.id, comment="Готово")

    items = student_review_items(db, student_id=user.id, role_rank=5)

    works = [i for i in items if i.domain == DOMAIN_BLOCK_WORK]
    assert len(works) == 1
    assert works[0].is_reviewed is False
    assert works[0].images == [FAKE_URL]
    assert works[0].text == "Готово"


def test_curator_can_mark_the_work_reviewed(admin_client, db, regular_user):
    # Сдачу заводим прямо в базе: `auth_client` и `admin_client` делят один
    # TestClient, и вторая фикстура перебила бы cookie первой.
    task = _task(db, regular_user)
    block = _upload_block(db, task)
    submission = TaskBlockSubmission(
        block_id=block.id, user_id=regular_user.id, submitted_at=TODAY_TS,
    )
    db.add(submission)
    db.commit()

    client, _ = admin_client
    resp = client.post(
        f"/cabinet/staff/students-review/block-work/{submission.id}/reviewed",
        json={"reviewed": True},
    )

    assert resp.status_code == 200
    db.refresh(submission)
    assert submission.reviewed_at is not None


def test_new_upload_returns_the_work_to_the_queue(auth_client, db):
    """Догрузил лист — работа снова ждёт куратора, а не висит проверенной."""
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _post(client, block.id)
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    submission.reviewed_at = submission.created_at
    db.commit()

    _post(client, block.id)

    db.refresh(submission)
    assert submission.reviewed_at is None


# ── конструктор ─────────────────────────────────────────────────────────────

def test_constructor_offers_the_upload_block(admin_client):
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert 'data-add-block="upload"' in page.text
    assert "Загрузить работы" in page.text


def test_constructor_saves_the_block_without_a_description(db, regular_user):
    """Блок самодостаточен: преподаватель может не писать пояснение.

    `sync_blocks` молча выбрасывает «пустые заготовки», и без явной ветки
    блок приёма работ попадал бы под это правило — учитель сохранил бы день,
    а блок исчез бы без единого сообщения.
    """
    task = _task(db, regular_user)

    blocks = sync_blocks(db, task_id=task.id, items=[{
        "block_type": BLOCK_UPLOAD,
        "title": "Пришлите работу",
        "is_required": True,
    }])
    db.commit()

    assert [b.block_type for b in blocks] == [BLOCK_UPLOAD]


def test_constructor_form_has_a_branch_for_the_block(admin_client):
    """У нового типа своя форма, а не общий хвост для вопросов."""
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert "type === 'upload'" in page.text


def test_constructor_offers_the_mock_exam_tile(admin_client):
    """Пробник вернули 07.09.2026: механика была цела, не работала кнопка."""
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert 'data-open-form="mock"' in page.text
    assert "Билеты по рисунку и композиции" in page.text
