"""Настройки доступности задания и блока в конструкторе (владелец 06.09.2026).

«Добавили контент, настроили кому доступно, настроили дату и проставили,
блокирует или нет дальнейшую выдачу». До 06.09 модель это уже умела, а
выставить руками было негде: форма конструктора отправляла блок без
обязательности, даты, тарифа и предмета, и всё сохранялось со значениями по
умолчанию.

Разбор созвона — 2026-09-06_разбор-созвона-03.09_процесс-добавления-заданий.md.
"""
from datetime import timedelta

from app.models.task_block import TaskBlock
from app.models.tracker import TrackerTask
from app.services.program import msk_date
from app.services.task_blocks import get_tariffs
from app.services.tz import today_msk

PROGRAM = "/cabinet/staff/program"


def _day(offset=14):
    return (today_msk() + timedelta(days=offset)).isoformat()


def _payload(**over):
    data = {
        "title": "Теория и задание",
        "description": "Смотрим видео, отвечаем",
        "subject": None,
        "is_required": True,
        "starts_on": None,
        "blocks": [],
        "audience": {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    }
    data.update(over)
    return data


def _block(**over):
    data = {
        "block_type": "text",
        "title": "Шаг",
        "body": "текст шага",
        "is_required": False,
        "subject": None,
        "tariffs": [],
        "opens_at": None,
        "bypass_sequence": False,
    }
    data.update(over)
    return data


def _task(db):
    return db.query(TrackerTask).order_by(TrackerTask.id.desc()).first()


def _blocks(db, task_id):
    return (
        db.query(TaskBlock)
        .filter(TaskBlock.task_id == task_id)
        .order_by(TaskBlock.sort_order)
        .all()
    )


# ── дата открытия задания ───────────────────────────────────────────────────

def test_task_without_start_date_opens_right_away(admin_client, db):
    client, _ = admin_client
    resp = client.post(f"{PROGRAM}/{_day()}/material", json=_payload())

    assert resp.status_code == 200
    assert _task(db).starts_at is None


def test_task_start_date_is_saved_as_msk_midnight(admin_client, db):
    """«Теория и задания откроются только с 23 сентября 00:00» — дата открытия
    задания целиком, отдельная от того, что ученик успел сделать раньше."""
    client, _ = admin_client
    opens_on = today_msk() + timedelta(days=10)

    resp = client.post(
        f"{PROGRAM}/{_day()}/material", json=_payload(starts_on=opens_on.isoformat())
    )

    assert resp.status_code == 200
    assert msk_date(_task(db).starts_at) == opens_on


def test_task_start_date_is_editable(admin_client, db):
    client, _ = admin_client
    client.post(f"{PROGRAM}/{_day()}/material", json=_payload())
    task = _task(db)
    moved_to = today_msk() + timedelta(days=12)

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/material",
        json=_payload(starts_on=moved_to.isoformat()),
    )

    assert resp.status_code == 200
    db.expire_all()
    assert msk_date(db.get(TrackerTask, task.id).starts_at) == moved_to


# ── настройки блока ─────────────────────────────────────────────────────────

def test_block_blocking_flag_is_saved(admin_client, db):
    """Галочка «блокирует дальнейшую выдачу» — на каждом блоке, а не одна общая."""
    client, _ = admin_client

    resp = client.post(
        f"{PROGRAM}/{_day()}/material",
        json=_payload(blocks=[_block(is_required=True), _block(title="Второй")]),
    )

    assert resp.status_code == 200
    blocks = _blocks(db, _task(db).id)
    assert [b.is_required for b in blocks] == [True, False]


def test_block_open_date_is_saved(admin_client, db):
    client, _ = admin_client
    opens_on = today_msk() + timedelta(days=5)

    resp = client.post(
        f"{PROGRAM}/{_day()}/material",
        json=_payload(blocks=[_block(opens_at=opens_on.isoformat())]),
    )

    assert resp.status_code == 200
    assert msk_date(_blocks(db, _task(db).id)[0].opens_at) == opens_on


def test_block_tariffs_are_saved(admin_client, db):
    """Кому доступно: «для уверенного максимума обязательно скидывать задание,
    для остальных тарифов — просто посмотреть видео».

    Владелец на созвоне называет тариф «Уверенный максимум», а в системе это
    два разных тарифа — «УВЕРЕННЫЙ» и «МАКСИМУМ» (`app/constants.py`).
    Расхождение вынесено в ВОПРОСЫ-ПО-ЛЕНТЕ.md.
    """
    client, _ = admin_client

    resp = client.post(
        f"{PROGRAM}/{_day()}/material",
        json=_payload(blocks=[_block(tariffs=["УВЕРЕННЫЙ"])]),
    )

    assert resp.status_code == 200
    block = _blocks(db, _task(db).id)[0]
    assert get_tariffs(db, [block.id])[block.id] == {"УВЕРЕННЫЙ"}


def test_block_subject_is_saved(admin_client, db):
    """Часть цикла идёт без деления на Рисунок и Композицию, часть — с делением."""
    client, _ = admin_client

    resp = client.post(
        f"{PROGRAM}/{_day()}/material",
        json=_payload(blocks=[_block(subject="Рисунок"), _block(title="Общий")]),
    )

    assert resp.status_code == 200
    blocks = _blocks(db, _task(db).id)
    assert [b.subject for b in blocks] == ["Рисунок", None]


def test_block_bypass_flag_is_saved(admin_client, db):
    client, _ = admin_client

    resp = client.post(
        f"{PROGRAM}/{_day()}/material",
        json=_payload(blocks=[_block(bypass_sequence=True)]),
    )

    assert resp.status_code == 200
    assert _blocks(db, _task(db).id)[0].bypass_sequence is True


def test_block_settings_survive_editing(admin_client, db):
    """Правка задания не сбрасывает настройки блоков в значения по умолчанию."""
    client, _ = admin_client
    opens_on = today_msk() + timedelta(days=3)
    client.post(
        f"{PROGRAM}/{_day()}/material",
        json=_payload(blocks=[_block(is_required=True, opens_at=opens_on.isoformat())]),
    )
    task = _task(db)
    block_id = _blocks(db, task.id)[0].id

    resp = client.post(
        f"{PROGRAM}/items/{task.id}/material",
        json=_payload(blocks=[
            _block(is_required=True, opens_at=opens_on.isoformat(), title="Шаг")
            | {"id": block_id}
        ]),
    )

    assert resp.status_code == 200
    db.expire_all()
    block = _blocks(db, task.id)[0]
    assert block.is_required is True
    assert msk_date(block.opens_at) == opens_on


# ── форма отдаёт поля ───────────────────────────────────────────────────────

def test_day_page_has_access_fields_for_task_and_block(admin_client, db):
    """Поля должны быть в разметке: без них преподаватель ничего не выставит,
    а блок молча сохранится со значениями по умолчанию."""
    client, _ = admin_client

    page = client.get(f"{PROGRAM}/{_day()}")

    assert "data-x-starts" in page.text
    for field in ("data-b-required", "data-b-opens", "data-b-subject",
                  "data-b-tariff", "data-b-bypass"):
        assert field in page.text
