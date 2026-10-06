"""Приём работ прямо в задании — блок «Домашнее задание» (владелец 07.09.2026).

Подпись блока с 16.09.2026 — «Домашнее задание», до этого была «Загрузить
работы»; значение типа в базе прежнее (`upload`).

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
from app.constants import TARIFF_CONFIDENT_MAX, TARIFF_SELF
from app.models.task_block import (
    BLOCK_PHOTO_UPLOAD,
    BLOCK_QUESTION,
    BLOCK_TIMED,
    BLOCK_UPLOAD,
    MAX_SUBMISSION_IMAGES,
    QUESTION_TEXT,
    TaskBlock,
    TaskBlockImage,
    TaskBlockSubmission,
    TaskBlockSubmissionImage,
    TaskBlockTariffDeadline,
)
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.models.tracker import TrackerTaskTariffDeadline
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
        due_at=day_bounds(TODAY + timedelta(days=2))[0] + timedelta(hours=6),
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


def _keep_task_open(db, task):
    """Второй обязательный шаг, который ученик ещё не сделал.

    Выполненное задание запирает сданную работу (владелец 02.10.2026), а
    задание из одного блока сдачи закрывается само этой же сдачей. Тестам про
    правку до срока и проверки нужно открытое задание.
    """
    return _upload_block(db, task, order=2)


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
    _keep_task_open(db, task)

    _post(client, block.id)
    _post(client, block.id)

    assert db.query(TaskBlockSubmission).count() == 1
    assert db.query(TaskBlockSubmissionImage).count() == 2


def test_student_can_edit_description_and_remove_photo(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _keep_task_open(db, task)
    _post(client, block.id, comment="Первый вариант")
    _post(client, block.id)
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    images = db.query(TaskBlockSubmissionImage).filter_by(submission_id=submission.id).order_by(TaskBlockSubmissionImage.id).all()

    edited = client.post(f"/cabinet/tracker/blocks/{block.id}/comment", json={"comment": "Исправил описание"})
    deleted = client.post(f"/cabinet/tracker/blocks/{block.id}/images/{images[0].id}/delete")

    assert edited.status_code == 200
    assert deleted.status_code == 200
    db.refresh(submission)
    assert submission.comment == "Исправил описание"
    assert db.query(TaskBlockSubmissionImage).filter_by(submission_id=submission.id).count() == 1


def test_deadline_blocks_first_upload_and_edit(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    block.closes_at = day_bounds(TODAY - timedelta(days=1))[0]
    db.commit()

    assert _post(client, block.id).status_code == 409
    assert get_submission(db, block_id=block.id, user_id=user.id) is None
    assert client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]["edit_reason"]


# ── срок приёма работ (владелец 27.09.2026) ─────────────────────────────────

def _deadline(db, block, when, *, tariff=None):
    """Срок приёма: общий у блока или свой у тарифа."""
    if tariff is None:
        block.submit_until = when
    else:
        db.add(TaskBlockTariffDeadline(
            block_id=block.id, tariff=tariff, submit_until=when,
        ))
    db.commit()


def test_submit_until_closes_upload_but_keeps_the_block_visible(auth_client, db):
    """Главное требование 27.09.2026: приём закрыт, задание на месте.

    До этого дня закрыть сдачу можно было только `closes_at`, а он запирает
    блок целиком — ученик терял и задание, и свою работу, и переписку с
    преподавателем. Здесь ученик всего этого не теряет: исчезают только
    кнопки загрузки, замены и удаления.
    """
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _post(client, block.id, comment="Сдал вовремя")
    _deadline(db, block, day_bounds(TODAY - timedelta(days=1))[0])

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]

    # Правка закрыта во всех трёх точках: загрузка, описание, удаление фото.
    assert _post(client, block.id).status_code == 409
    images = db.query(TaskBlockSubmissionImage).all()
    assert client.post(
        f"/cabinet/tracker/blocks/{block.id}/comment", json={"comment": "Переделал"}
    ).status_code == 409
    assert client.post(
        f"/cabinet/tracker/blocks/{block.id}/images/{images[0].id}/delete"
    ).status_code == 409
    # А блок при этом отдаётся с работой и описанием — он не заперт.
    assert payload["edit_reason"]
    assert payload["submitted_files"]
    assert payload["submitted_comment"] == "Сдал вовремя"


def test_expired_submit_until_still_holds_the_rest_of_the_feed(db, regular_user):
    """Обязательный блок с истёкшим сроком держит хвост ленты (владелец
    06.10.2026: «досдать свыше срока всегда можно»). Сдать его можно, значит
    тупика нет — очередь ждёт сдачи. До 06.10.2026 срок запирал сдачу, и
    такой блок хвост отпускал."""
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    first = _upload_block(db, task, order=1)
    _block_after = TaskBlock(
        task_id=task.id, block_type="text", body="Что дальше", sort_order=2,
    )
    db.add(_block_after)
    db.commit()
    _deadline(db, first, day_bounds(TODAY - timedelta(days=1))[0])

    steps = build_cycle_feed(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )

    assert [s["status"] for s in steps] == ["current", "locked"]


def test_first_upload_after_the_deadline_is_accepted(auth_client, db):
    """Владелец 06.10.2026: «досдать свыше срока всегда можно, но просрок
    дедлайна записывается… ЭТО ПРАВИЛО!!!». Первую сдачу принимают,
    повторную (замену) после срока — нет: опоздание пишется по первой."""
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _keep_task_open(db, task)
    _deadline(db, block, day_bounds(TODAY - timedelta(days=1))[0])

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]
    assert payload["edit_reason"] is None
    assert payload["late_allowed"] is True

    assert _post(client, block.id).status_code == 200
    assert _post(client, block.id).status_code == 409


def test_tariff_deadline_overrides_the_common_one(auth_client, db):
    """«Для одного тарифа один дедлайн, для другого тарифа другой»."""
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _keep_task_open(db, task)
    # Общий срок прошёл, а у тарифа ученика он ещё впереди.
    _deadline(db, block, day_bounds(TODAY - timedelta(days=1))[0])
    _deadline(
        db, block, day_bounds(TODAY + timedelta(days=1))[0], tariff=user.tariff,
    )

    assert _post(client, block.id).status_code == 200
    assert client.get(
        f"/cabinet/tracker/tasks/{task.id}/blocks"
    ).json()["blocks"][0]["edit_reason"] is None


def _question_block(db, task):
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_QUESTION, question_type=QUESTION_TEXT,
        body="Что получилось?", sort_order=1, is_required=True,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _answer(client, task, block):
    return client.post(
        f"/cabinet/tracker/tasks/{task.id}/blocks",
        json={"answers": [{"block_id": block.id, "text": "Штриховка"}]},
    )


def test_tariff_deadline_of_a_question_overrides_the_common_one(auth_client, db):
    """Общий роут ответов (вопрос, шкала, правила) до 28.09.2026 сверялся
    только с общим сроком: продлённый тарифу срок ученика не пускал."""
    client, user = auth_client
    task = _task(db, user)
    block = _question_block(db, task)
    _deadline(db, block, day_bounds(TODAY - timedelta(days=1))[0])
    _deadline(
        db, block, day_bounds(TODAY + timedelta(days=1))[0], tariff=user.tariff,
    )

    assert _answer(client, task, block).status_code == 200


def test_shortened_tariff_deadline_closes_the_question(auth_client, db):
    """Обратная сторона: общий срок впереди, а у тарифа ученика уже вышел.
    Первый ответ после срока принимается (владелец 06.10.2026), а
    переответить уже нельзя — по сроку тарифа, не по общему."""
    client, user = auth_client
    task = _task(db, user)
    block = _question_block(db, task)
    _deadline(db, block, day_bounds(TODAY + timedelta(days=1))[0])
    _deadline(
        db, block, day_bounds(TODAY - timedelta(days=1))[0], tariff=user.tariff,
    )

    assert _answer(client, task, block).status_code == 200
    resp = _answer(client, task, block)

    assert resp.status_code == 409
    # Дату срока не называем (владелец 02.10.2026): её называет текст задания.
    assert resp.json()["detail"] == "Срок сдачи прошёл. Изменить работу нельзя."


def test_task_level_tariff_deadline_closes_the_question(auth_client, db):
    """Срок тарифа на всё задание тоже доходит до ответа на вопрос."""
    client, user = auth_client
    task = _task(db, user)
    block = _question_block(db, task)
    db.add(TrackerTaskTariffDeadline(
        task_id=task.id, tariff=user.tariff,
        submit_until=day_bounds(TODAY - timedelta(days=1))[0],
    ))
    db.commit()

    assert _answer(client, task, block).status_code == 200
    assert _answer(client, task, block).status_code == 409


def _day_task_in_the_past(db, owner):
    """Задание с экрана дня: `due_at` — 23:59 его дня, и день уже прошёл."""
    task = _task(db, owner)
    task.due_at = day_bounds(TODAY - timedelta(days=2))[1] - timedelta(minutes=1)
    db.commit()
    return task


def test_submit_deadline_extends_past_the_day_of_the_task(auth_client, db):
    """Владелец 28.09.2026: у задания стоит срок сдачи до числа и времени —
    он и запирает. До этого день задания (23:59) запирал раньше срока, и
    продлить приём за конец дня было нельзя: «Срок сдачи истёк 25.09 в 23:59»
    при сроке до 30.09."""
    client, user = auth_client
    task = _day_task_in_the_past(db, user)
    task.submit_until = day_bounds(TODAY + timedelta(days=2))[0]
    db.commit()
    block = _upload_block(db, task)

    assert _post(client, block.id).status_code == 200


def test_block_deadline_also_replaces_the_day_of_the_task(auth_client, db):
    client, user = auth_client
    task = _day_task_in_the_past(db, user)
    block = _upload_block(db, task)
    _deadline(db, block, day_bounds(TODAY + timedelta(days=2))[0])

    assert _post(client, block.id).status_code == 200


def test_without_submit_deadline_the_day_of_the_task_still_closes(auth_client, db):
    """Срока сдачи нет нигде — работает прежнее правило: день задания. С
    06.10.2026 он, как и срок, запирает только правку сданного."""
    client, user = auth_client
    task = _day_task_in_the_past(db, user)
    block = _upload_block(db, task)
    _keep_task_open(db, task)

    assert _post(client, block.id).status_code == 200
    assert _post(client, block.id).status_code == 409


def test_tariff_without_its_own_row_lives_by_the_common_deadline(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _deadline(db, block, day_bounds(TODAY - timedelta(days=1))[0])
    # Строка заведена чужому тарифу — ученика она не касается.
    _deadline(
        db, block, day_bounds(TODAY + timedelta(days=1))[0],
        tariff=TARIFF_CONFIDENT_MAX if user.tariff != TARIFF_CONFIDENT_MAX else TARIFF_SELF,
    )
    _keep_task_open(db, task)

    # Первая сдача после срока принимается, замена — нет (06.10.2026).
    assert _post(client, block.id).status_code == 200
    assert _post(client, block.id).status_code == 409


def test_empty_tariff_row_means_no_deadline_at_all(auth_client, db):
    """Строка тарифа с пустым сроком — «приём бессрочный», а не «как у всех».

    Отличить одно от другого можно только по наличию строки: значение в ней
    пустое в обоих случаях (см. `TaskBlockTariffDeadline`).
    """
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _deadline(db, block, day_bounds(TODAY - timedelta(days=1))[0])
    _deadline(db, block, None, tariff=user.tariff)

    assert _post(client, block.id).status_code == 200


def test_student_sees_the_deadline_before_it_passes(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _deadline(db, block, day_bounds(TODAY + timedelta(days=1))[0])

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]

    assert payload["submit_deadline"]
    assert payload["edit_reason"] is None


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


# ── фото + сдача работы (владелец 12.09.2026) ───────────────────────────────

def test_photo_upload_block_accepts_the_work_in_place(auth_client, db):
    """Комбинированный блок закрывается тем же приёмом, что «Загрузить
    работы» — фото-задание к закрытию отношения не имеет."""
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task, block_type=BLOCK_PHOTO_UPLOAD)
    db.add(TaskBlockImage(block_id=block.id, image_s3_url="https://s3.example.com/task.jpg"))
    db.commit()

    resp = _post(client, block.id, comment="Готово")

    assert resp.status_code == 200
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    assert submission is not None
    assert get_state(db, block_id=block.id, user_id=user.id).status == "done"


def test_photo_upload_block_payload_carries_task_photos_and_upload_endpoint(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task, block_type=BLOCK_PHOTO_UPLOAD)
    db.add(TaskBlockImage(block_id=block.id, image_s3_url="https://s3.example.com/task.jpg"))
    db.commit()

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()

    item = payload["blocks"][0]
    assert item["images"] == [{"url": "https://s3.example.com/task.jpg"}]
    assert item["upload_endpoint"] == f"/cabinet/tracker/blocks/{block.id}/upload"
    assert item["done"] is False


# ── лента ───────────────────────────────────────────────────────────────────

def test_feed_opens_the_tail_after_the_work_is_sent(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _upload_block(db, task)
    # Хвост — шаг, который можно отметить, а не текст: с 01.10.2026 текст
    # закрытого задания считается сделанным, и сдача единственной работы
    # закрыла бы задание вместе с хвостом — «открылась ли очередь» не проверить.
    second = TaskBlock(
        task_id=task.id, block_type="upload", body="Следующий шаг", sort_order=2,
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


def test_curator_can_save_editable_feedback_for_work(admin_client, db, regular_user):
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
        json={"reviewed": True, "comment": "Сильная работа"},
    )

    assert resp.status_code == 200
    assert resp.json()["comment"] == "Сильная работа"
    db.refresh(submission)
    assert submission.review_comment == "Сильная работа"

    items = student_review_items(db, student_id=regular_user.id, role_rank=5)
    work = next(item for item in items if item.domain == DOMAIN_BLOCK_WORK)
    assert work.review_comment == "Сильная работа"


def test_reviewed_work_cannot_be_changed(auth_client, db):
    """После проверки работа остаётся неизменной даже до дедлайна."""
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _post(client, block.id)
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    submission.reviewed_at = submission.created_at
    submission.score = 91
    submission.scored_at = submission.created_at
    db.commit()

    response = _post(client, block.id)

    db.refresh(submission)
    assert response.status_code == 409
    assert submission.reviewed_at is not None
    assert submission.score == 91
    assert submission.scored_at is not None


def test_curator_reply_locks_work_before_review_flag(auth_client, db, user_factory):
    client, user = auth_client
    task = _task(db, user)
    block = _upload_block(db, task)
    _post(client, block.id)
    submission = get_submission(db, block_id=block.id, user_id=user.id)
    curator = user_factory(vk_id=777_022, name="Куратор", role_name="куратор")
    feedback = TaskBlockFeedback(submission_id=submission.id, curator_id=curator.id)
    db.add(feedback)
    db.flush()
    # Сообщение — явно после сдачи и в той же зоне, что `submitted_at`. Правку
    # запирают только ответы после текущей сдачи (8fda3fd, 21.09.2026), а
    # SQLite теряет зону: сдача пишется по Москве (`now_msk`), сообщение по UTC,
    # и без этого ответ куратора в тестовой базе оказывался на три часа раньше
    # сдачи. На проде (Postgres, timestamptz) сравнение и так честное.
    db.add(TaskBlockFeedbackMessage(
        feedback_id=feedback.id, sender_id=curator.id, sender_role="curator", text="Исправь",
        created_at=submission.submitted_at + timedelta(minutes=1),
    ))
    db.commit()

    assert _post(client, block.id).status_code == 409
    assert client.post(f"/cabinet/tracker/blocks/{block.id}/comment", json={"comment": "Исправил"}).status_code == 409


# ── конструктор ─────────────────────────────────────────────────────────────

def test_constructor_no_longer_offers_a_standalone_upload_button(admin_client):
    """Кнопка «+ Домашнее задание» на BLOCK_UPLOAD снята 17.09.2026 — слита
    с «Фото + сдача работы» в один тип BLOCK_PHOTO_UPLOAD. Тип BLOCK_UPLOAD
    жив в базе (старые блоки), подпись «Домашнее задание» не менялась."""
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert 'data-add-block="upload"' not in page.text
    assert "Домашнее задание" in page.text
    assert "Загрузить работы" not in page.text


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


def test_constructor_offers_the_photo_upload_block(admin_client):
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert 'data-add-block="photo_upload"' in page.text
    # Подпись сменилась с «Фото + сдача работы» на «Домашнее задание»
    # (владелец 16.09.2026: слить фотоблок и «Домашнее задание» в один тип).
    assert "type === 'photo_upload'" in page.text



def test_answer_after_the_deadline_is_accepted_and_marked_for_review(auth_client, db):
    """Владелец 06.10.2026: «досдать свыше срока всегда можно, но просрок
    дедлайна записывается и показан при проверке задания». Первый ответ
    после срока принят, на проверке — приписка «(сдано после срока)»."""
    from app.services.review_aggregate import DOMAIN_TASK_BLOCK, student_review_items

    client, user = auth_client
    task = _task(db, user)
    block = _question_block(db, task)
    _deadline(db, block, day_bounds(TODAY - timedelta(days=1))[0])

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]
    assert payload["edit_reason"] is None
    assert _answer(client, task, block).status_code == 200

    items = student_review_items(db, student_id=user.id, role_rank=5)
    answer = next(i for i in items if i.domain == DOMAIN_TASK_BLOCK)
    assert answer.title == f"{task.title} (сдано после срока)"
