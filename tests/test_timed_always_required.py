"""Контрольная на время обязательна всегда (владелец 06.10.2026).

«В актуальном образовательном пространстве ребёнку должно быть открыто то,
где он застрял. Если не сдал контрольные, то застрял на этом и у него висит
это и всё остальное недоступно.»

На проде у «Контрольной по рисунку» шагу не поставили галочку «Блокирует
дальнейшую выдачу»: «Завершить задание» закрывало задание без сдачи, а
закрытое задание снимает долг цикла. Теперь флаг в базе для контрольной не
важен — правило в `task_blocks.is_block_required_for_user`.
"""
from app.models.task_block import BLOCK_PHOTO_UPLOAD, BLOCK_TIMED, TaskBlock
from app.models.tracker import TrackerTask
from app.services.task_blocks import (
    completion_button_needed,
    feed_state,
    get_blocks,
    is_block_required_for_user,
    sync_blocks,
    unfinished_required_steps,
)


def _task(db):
    task = TrackerTask(title="Контрольная по рисунку", kind="material", is_required=True)
    db.add(task)
    db.flush()
    return task


def _timed_without_checkbox(db, task):
    """Как на проде 06.10.2026: блок записан в базу с `is_required=False`."""
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_TIMED, title="Контрольная",
        sort_order=0, is_required=False, time_limit_minutes=75,
    )
    after = TaskBlock(
        task_id=task.id, block_type=BLOCK_PHOTO_UPLOAD, title="После контрольной",
        sort_order=1, is_required=False,
    )
    db.add_all([block, after])
    db.commit()
    return block


def test_timed_block_is_required_without_checkbox(db):
    task = _task(db)
    block = _timed_without_checkbox(db, task)

    assert is_block_required_for_user(block, is_intake_student=False)
    assert is_block_required_for_user(block, is_intake_student=True)


def test_unsubmitted_timed_block_stops_complete_button_and_locks_below(db, regular_user):
    """Несданная контрольная держит «Завершить задание» и всё ниже неё."""
    task = _task(db)
    _timed_without_checkbox(db, task)

    steps = unfinished_required_steps(
        db, task_id=task.id, user_id=regular_user.id, user_tariff=regular_user.tariff,
    )
    assert [step.title for step in steps] == ["Контрольная"]

    feed = feed_state(
        db, task_id=task.id, user_id=regular_user.id, user_tariff=regular_user.tariff,
    )
    assert [(e["block"].title, e["status"]) for e in feed] == [
        ("Контрольная", "current"), ("После контрольной", "locked"),
    ]


def test_task_of_only_timed_block_closes_itself(db, regular_user):
    """Кнопка «Завершить задание» не нужна, если все шаги обязательные, — и
    контрольная без галочки теперь в их числе: задание закроет сама сдача."""
    task = _task(db)
    db.add(TaskBlock(
        task_id=task.id, block_type=BLOCK_TIMED, title="Контрольная",
        sort_order=0, is_required=False, time_limit_minutes=75,
    ))
    db.commit()

    assert not completion_button_needed(
        db, task_id=task.id, user_id=regular_user.id, user_tariff=regular_user.tariff,
    )


def test_constructor_save_writes_timed_as_required(db):
    """Сохранение из конструктора пишет флаг и в базу: галочка в форме стоит
    и не снимается, а пришедшее `False` не должно её переписать."""
    task = _task(db)
    sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_TIMED, "title": "Контрольная", "is_required": False,
         "time_limit_minutes": 75},
        {"block_type": BLOCK_PHOTO_UPLOAD, "title": "Работа", "is_required": False},
    ])
    db.commit()

    assert [(b.block_type, b.is_required) for b in get_blocks(db, task.id)] == [
        (BLOCK_TIMED, True), (BLOCK_PHOTO_UPLOAD, False),
    ]
