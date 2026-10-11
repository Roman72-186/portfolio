"""Задание закрывается само, когда ученик отметил все свои шаги (владелец 30.09.2026).

Прецедент: задание из одних необязательных блоков закрывалось только кнопкой
«Завершить задание». Её не нажимали, а должнику прошлого цикла её не
показывали вовсе (архив), — цикл 3 «не сдали» 27 человек, отметивших кружки.
"""
from datetime import timedelta

from app.models.task_block import (
    BLOCK_LINK, BLOCK_PHOTO, BLOCK_TEXT, TaskBlock, TaskBlockState, TaskBlockTariff,
)
from app.models.tracker import STATUS_DONE, TrackerTaskState
from app.services.program import day_bounds
from app.services.task_blocks import (
    close_block_for_user, close_tasks_with_all_blocks_done, maybe_close_task_by_blocks,
)
from app.services.tracker import create_task
from app.services.tz import today_msk


def _task(db, owner, *, kind="material"):
    task = create_task(
        db, title="Декомпозиции архитектуры", user_id=owner.id, kind=kind,
        due_at=day_bounds(today_msk())[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _block(db, task, block_type=BLOCK_PHOTO, *, order=1, tariff=None, hidden=False):
    block = TaskBlock(
        task_id=task.id, block_type=block_type, title=f"Шаг {order}", body="текст",
        sort_order=order, is_required=False, hidden_until_done=hidden,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    if tariff:
        db.add(TaskBlockTariff(block_id=block.id, tariff=tariff))
        db.commit()
    return block


def _state(db, task, user):
    return (
        db.query(TrackerTaskState)
        .filter_by(task_id=task.id, user_id=user.id)
        .one_or_none()
    )


def _mark(db, block, user):
    close_block_for_user(db, block=block, user_id=user.id, source="photo_confirmed")
    db.commit()


def test_last_step_closes_the_task(db, regular_user):
    task = _task(db, regular_user)
    first = _block(db, task, order=1)
    second = _block(db, task, order=2)

    _mark(db, first, regular_user)
    assert _state(db, task, regular_user) is None

    _mark(db, second, regular_user)
    state = _state(db, task, regular_user)
    assert state is not None and state.status == STATUS_DONE
    assert state.completion_source == "blocks_done"


def test_block_of_other_tariff_does_not_hold_the_task(db, regular_user):
    regular_user.tariff = "Я С ВАМИ"
    db.commit()
    task = _task(db, regular_user)
    mine = _block(db, task, order=1)
    _block(db, task, order=2, tariff="УВЕРЕННЫЙ МАКСИМУМ")

    _mark(db, mine, regular_user)

    assert _state(db, task, regular_user).status == STATUS_DONE


def test_other_tariff_still_needs_its_own_block(db, regular_user, user_factory):
    task = _task(db, regular_user)
    common = _block(db, task, order=1)
    _block(db, task, order=2, tariff="УВЕРЕННЫЙ МАКСИМУМ")
    maximum = user_factory(vk_id=771_101, name="Максимум", tariff="УВЕРЕННЫЙ МАКСИМУМ")

    _mark(db, common, maximum)

    assert _state(db, task, maximum) is None


def test_text_link_and_hidden_blocks_do_not_hold_the_task(db, regular_user):
    task = _task(db, regular_user)
    _block(db, task, BLOCK_TEXT, order=1)
    _block(db, task, BLOCK_LINK, order=2)
    _block(db, task, order=3, hidden=True)
    photo = _block(db, task, order=4)

    _mark(db, photo, regular_user)

    assert _state(db, task, regular_user).status == STATUS_DONE


def test_task_of_text_only_never_closes_itself(db, regular_user):
    task = _task(db, regular_user)
    _block(db, task, BLOCK_TEXT, order=1)

    assert maybe_close_task_by_blocks(db, task.id, regular_user.id) is False
    assert _state(db, task, regular_user) is None


def test_homework_is_not_closed_by_blocks(db, regular_user):
    task = _task(db, regular_user, kind="homework")
    photo = _block(db, task, order=1)

    _mark(db, photo, regular_user)

    assert _state(db, task, regular_user) is None


def test_closed_task_stays_closed(db, regular_user):
    task = _task(db, regular_user)
    photo = _block(db, task, order=1)
    _mark(db, photo, regular_user)
    _block(db, task, order=2)  # шаг добавили после закрытия

    assert maybe_close_task_by_blocks(db, task.id, regular_user.id) is False
    assert _state(db, task, regular_user).status == STATUS_DONE


def test_backfill_closes_tasks_marked_before_autoclose(db, regular_user, user_factory):
    task = _task(db, regular_user)
    photo = _block(db, task, order=1)
    other = user_factory(vk_id=771_102, name="Не отметил")
    # Как до 30.09.2026: блок закрыт, задание — нет.
    db.add(TaskBlockState(block_id=photo.id, user_id=regular_user.id, status=STATUS_DONE))
    db.commit()
    assert _state(db, task, regular_user) is None

    closed = close_tasks_with_all_blocks_done(db)
    db.commit()

    assert closed == 1
    assert _state(db, task, regular_user).status == STATUS_DONE
    assert _state(db, task, other) is None
    assert close_tasks_with_all_blocks_done(db) == 0


def test_circle_route_closes_the_task(auth_client, db):
    client, student = auth_client
    task = _task(db, student)
    photo = _block(db, task, order=1)

    resp = client.post(f"/cabinet/tracker/blocks/{photo.id}/done")

    assert resp.status_code == 200
    assert _state(db, task, student).status == STATUS_DONE


# ── Необязательное проход не держит (владелец 11.10.2026) ─────────────────

def test_required_steps_done_close_the_task_with_optional_left(db, regular_user):
    """Ролики без галочки «Блокирует дальнейшую выдачу» «не должны блокировать
    дальнейший проход». Прод 11.10.2026: 14 учеников сдали всё обязательное, а
    неделя после была заперта долгом из-за неотмеченного ролика или
    необязательной домашки."""
    task = _task(db, regular_user)
    required = _block(db, task, order=1)
    required.is_required = True
    db.commit()
    _block(db, task, order=2)  # необязательный

    _mark(db, required, regular_user)

    state = _state(db, task, regular_user)
    assert state is not None and state.status == STATUS_DONE
    assert state.completion_source == "required_done"


def test_task_without_required_steps_still_waits_for_every_step(db, regular_user):
    task = _task(db, regular_user)
    first = _block(db, task, order=1)
    _block(db, task, order=2)

    _mark(db, first, regular_user)

    assert _state(db, task, regular_user) is None


def test_optional_task_flag_keeps_the_old_rule(db, regular_user):
    """Флаг задания над флагами блоков: задание не обязательно — обязательных
    шагов нет, автозакрытие ждёт всех."""
    task = _task(db, regular_user)
    task.is_required = False
    required = _block(db, task, order=1)
    required.is_required = True
    db.commit()
    _block(db, task, order=2)

    _mark(db, required, regular_user)

    assert _state(db, task, regular_user) is None


def test_backfill_closes_tasks_whose_required_steps_were_already_done(db, regular_user):
    task = _task(db, regular_user)
    required = _block(db, task, order=1)
    required.is_required = True
    db.commit()
    _block(db, task, order=2)
    db.add(TaskBlockState(block_id=required.id, user_id=regular_user.id, status=STATUS_DONE))
    db.commit()

    assert close_tasks_with_all_blocks_done(db) == 1
    db.commit()  # бэкфилл не коммитит сам
    assert _state(db, task, regular_user).completion_source == "required_done"


def test_required_done_task_lifts_the_cycle_debt_but_not_the_stage_archive(db, regular_user):
    """Долг недели снят, а этап в архив не уходит, пока не сделано и
    необязательное (владелец 06.10.2026: «не сделал вообще всё, включая
    необязательное»)."""
    from tests.test_cycle_lock import TODAY, _program

    from app.services.cycle_feed import stage_finished_by_user
    from app.services.tracker import close_task_for_user, cycle_debt

    stage, (c2, _c3, _c4), (t2, t3, t4) = _program(db, regular_user)
    for other in (t3, t4):  # этап держит только t2
        close_task_for_user(db, other, regular_user.id, source="manual")
    required = _block(db, t2, order=1)
    required.is_required = True
    db.commit()
    optional = _block(db, t2, order=2)

    assert cycle_debt(db, regular_user.id, TODAY)["cycle"].id == c2.id
    _mark(db, required, regular_user)

    debt = cycle_debt(db, regular_user.id, TODAY)
    assert debt is None or debt["cycle"].id != c2.id
    kwargs = dict(user_id=regular_user.id, user_tariff=regular_user.tariff, stage=stage, today=TODAY)
    assert not stage_finished_by_user(db, **kwargs)

    _mark(db, optional, regular_user)
    assert stage_finished_by_user(db, **kwargs)
