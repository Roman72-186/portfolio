"""Быстрая правка срока приёма работ из списка заданий (владелец 27.09.2026).

«Чтобы Лиза могла спокойно заходить, редактировать в этом задании дедлайн…
мы можем его смещать» — без открытия формы задания и без оглядки на то,
прошёл ли день, к которому задание привязано.
"""

from datetime import timedelta

from unittest.mock import patch

from app.constants import TARIFF_CONFIDENT_MAX, TARIFF_SELF
from app.models.task_block import (
    BLOCK_PHOTO_UPLOAD,
    BLOCK_UPLOAD,
    TaskBlock,
    TaskBlockTariffDeadline,
)
from app.models.tracker import TrackerTask
from app.services import s3 as s3_service
from app.services.activity_stats import get_deadline_stats
from app.services.program import day_bounds
from app.services.tracker import create_task
from app.services.tz import today_msk

TODAY = today_msk()
FAKE_URL = "https://s3.example.com/work.jpg"
DEADLINE_URL = "/cabinet/staff/program/blocks/{}/deadline"
EVERYONE = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}


def _staff(client, user_factory, session_factory, *, role_name="админ", vk_id=700_901):
    staff = user_factory(
        vk_id=vk_id, name="Главный", is_admin=role_name == "админ", role_name=role_name,
    )
    client.cookies.set("session_id", session_factory(staff).id)
    return staff


def _task_with_block(db, owner, *, day, block_type=BLOCK_UPLOAD):
    task = create_task(
        db, title="Эскиз третьего цикла", user_id=owner.id, kind="material",
        due_at=day_bounds(day)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    block = TaskBlock(
        task_id=task.id, block_type=block_type, title="Пришлите работу", sort_order=1,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return task, block


def test_deadline_is_saved_without_opening_the_item_form(
    client, db, user_factory, session_factory
):
    staff = _staff(client, user_factory, session_factory)
    _, block = _task_with_block(db, staff, day=TODAY)

    resp = client.post(
        DEADLINE_URL.format(block.id),
        json={"submit_until": "2026-09-28T09:30", "submit_deadlines": []},
        headers={"X-CSRF-Token": "x"},
    )

    assert resp.status_code == 200, resp.text
    db.refresh(block)
    # 09:30 МСК — 06:30 UTC.
    assert block.submit_until.hour == 6 and block.submit_until.minute == 30
    assert resp.json()["submit_until"] == "2026-09-28T09:30"


def test_tariff_deadlines_are_saved_and_replaced(client, db, user_factory, session_factory):
    """Свой срок по тарифу заводится и полностью пересобирается при правке —
    как тарифы видимости, снести и собрать заново."""
    staff = _staff(client, user_factory, session_factory)
    _, block = _task_with_block(db, staff, day=TODAY)

    client.post(
        DEADLINE_URL.format(block.id),
        json={
            "submit_until": "2026-09-28T09:30",
            "submit_deadlines": [
                {"tariff": TARIFF_SELF, "submit_until": "2026-09-29T21:00"},
                # Пустое время — «этому тарифу приём бессрочный».
                {"tariff": TARIFF_CONFIDENT_MAX, "submit_until": None},
            ],
        },
        headers={"X-CSRF-Token": "x"},
    )
    rows = {
        row.tariff: row.submit_until
        for row in db.query(TaskBlockTariffDeadline).all()
    }
    assert set(rows) == {TARIFF_SELF, TARIFF_CONFIDENT_MAX}
    assert rows[TARIFF_CONFIDENT_MAX] is None

    client.post(
        DEADLINE_URL.format(block.id),
        json={"submit_until": None, "submit_deadlines": []},
        headers={"X-CSRF-Token": "x"},
    )

    db.refresh(block)
    assert block.submit_until is None
    assert db.query(TaskBlockTariffDeadline).count() == 0


def test_unknown_tariff_is_ignored_and_does_not_break_saving(
    client, db, user_factory, session_factory
):
    """Неизвестный тариф молча отбрасывается — та же конвенция, что у
    тарифов видимости: справочник переименовывается правкой `constants.py`,
    и форма не должна становиться вторым местом обновления."""
    staff = _staff(client, user_factory, session_factory)
    _, block = _task_with_block(db, staff, day=TODAY)

    resp = client.post(
        DEADLINE_URL.format(block.id),
        json={
            "submit_until": None,
            "submit_deadlines": [
                {"tariff": "ТАРИФ КОТОРОГО НЕТ", "submit_until": "2026-09-29T21:00"},
                {"tariff": TARIFF_SELF, "submit_until": "2026-09-29T21:00"},
            ],
        },
        headers={"X-CSRF-Token": "x"},
    )

    assert resp.status_code == 200, resp.text
    assert [r.tariff for r in db.query(TaskBlockTariffDeadline).all()] == [TARIFF_SELF]


def test_deadline_of_a_past_day_item_is_editable(client, db, user_factory, session_factory):
    """Главный смысл кнопки: продлевают уже наступивший срок.

    Обычная правка элемента прошедшего дня запрещена (`_guard_editable`,
    «Прошедший день можно только смотреть»), и если бы срок подчинялся тому же
    правилу, кнопка не работала бы ровно тогда, когда нужна.
    """
    staff = _staff(client, user_factory, session_factory)
    _, block = _task_with_block(db, staff, day=TODAY - timedelta(days=3))

    resp = client.post(
        DEADLINE_URL.format(block.id),
        json={"submit_until": "2026-09-30T23:59", "submit_deadlines": []},
        headers={"X-CSRF-Token": "x"},
    )

    assert resp.status_code == 200, resp.text
    db.refresh(block)
    assert block.submit_until is not None


def test_any_block_type_takes_a_deadline(client, db, user_factory, session_factory):
    """Срок ставится блоку любого типа (владелец 27.09.2026, второй заход:
    «добавить в доступность блока и для всех заданий»).

    Первая версия дня принимала срок только у блоков сдачи и отвечала 404 на
    остальные — то правило снято. Что срок делает, решает
    `DEADLINE_BLOCKS_COMPLETION`: у видео и текста он не запрещает отметить
    выполнение, а идёт в статистику «до срока / после срока».
    """
    staff = _staff(client, user_factory, session_factory)
    task, _ = _task_with_block(db, staff, day=TODAY)
    text_block = TaskBlock(
        task_id=task.id, block_type="text", body="Просто текст", sort_order=2,
    )
    db.add(text_block)
    db.commit()

    resp = client.post(
        DEADLINE_URL.format(text_block.id),
        json={"submit_until": "2026-09-28T09:30", "submit_deadlines": []},
        headers={"X-CSRF-Token": "x"},
    )

    assert resp.status_code == 200, resp.text
    db.refresh(text_block)
    assert text_block.submit_until is not None


def test_task_deadline_applies_to_blocks_without_their_own(
    client, db, user_factory, session_factory
):
    """Срок на задание целиком — и блоки без своего живут по нему."""
    staff = _staff(client, user_factory, session_factory)
    task, block = _task_with_block(db, staff, day=TODAY)

    resp = client.post(
        f"/cabinet/staff/program/items/{task.id}/deadline",
        json={
            "submit_until": "2026-09-28T09:30",
            "submit_deadlines": [{"tariff": TARIFF_SELF, "submit_until": "2026-09-29T21:00"}],
        },
        headers={"X-CSRF-Token": "x"},
    )

    assert resp.status_code == 200, resp.text
    db.refresh(task)
    assert (task.submit_until.hour, task.submit_until.minute) == (6, 30)
    assert resp.json()["submit_until"] == "2026-09-28T09:30"
    # Блок своего срока не получил — он и не должен.
    db.refresh(block)
    assert block.submit_until is None


def test_curator_cannot_move_the_deadline(client, db, user_factory, session_factory):
    """Сроки правит Главный преподаватель, как и остальное в конструкторе
    (`require_admin_role`, ранг ≥ 4)."""
    owner = _staff(client, user_factory, session_factory, vk_id=700_902)
    _, block = _task_with_block(db, owner, day=TODAY)
    _staff(client, user_factory, session_factory, role_name="куратор", vk_id=700_903)

    resp = client.post(
        DEADLINE_URL.format(block.id),
        json={"submit_until": "2026-09-28T09:30", "submit_deadlines": []},
        headers={"X-CSRF-Token": "x"},
    )

    assert resp.status_code == 403


def test_deadline_badge_is_gone_from_both_constructor_screens(
    client, db, user_factory, session_factory
):
    """Плашки срока нет ни на экране дня, ни на экране заданий цикла.

    Снята 28.09.2026 по решению владельца: она рисовалась под каждым заданием
    строкой на сам элемент и на каждый его блок со сроком (у «Формообразования
    узлов» вышло пять строк), и список заданий за ней не читался. Владелец:
    «сроки не должны быть здесь, сначала все задания, а потом уже в блоке
    настраиваем, когда, что и сроки». Сроки правятся внутри задания, в
    «Доступности блока» (партиал `program_item_deadline_fields.html`).

    Сторож парный и перевёрнутый: экранов конструктора два, и вернуть плашку на
    один из них, забыв про второй, — ровно та ошибка, что уже случалась с
    кнопками блоков.
    """
    staff = _staff(client, user_factory, session_factory)
    day = TODAY + timedelta(days=1)
    _, block = _task_with_block(db, staff, day=day, block_type=BLOCK_PHOTO_UPLOAD)
    client.post(
        DEADLINE_URL.format(block.id),
        json={"submit_until": "2026-09-28T09:30", "submit_deadlines": []},
        headers={"X-CSRF-Token": "x"},
    )

    day_page = client.get(f"/cabinet/staff/program/{day.isoformat()}")

    assert day_page.status_code == 200
    assert "data-deadline-endpoint" not in day_page.text
    assert "Приём до 28.09.2026 в 09:30" not in day_page.text

    # Тот же блок, но внутри цикла — второй экран конструктора.
    cycle = client.post(
        "/cabinet/staff/program/cycles",
        json={
            "title": "Цикл 3", "description": None,
            "starts_on": TODAY.isoformat(),
            "ends_on": (TODAY + timedelta(days=5)).isoformat(),
            "is_published": True,
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert cycle.status_code == 200, cycle.text
    cycle_id = cycle.json()["cycle_id"]
    cycle_task, cycle_block = _task_with_block(db, staff, day=TODAY)
    cycle_task.topic_id = cycle_id
    cycle_task.due_at = None
    db.commit()
    client.post(
        DEADLINE_URL.format(cycle_block.id),
        json={"submit_until": "2026-09-28T09:30", "submit_deadlines": []},
        headers={"X-CSRF-Token": "x"},
    )

    cycle_page = client.get(f"/cabinet/staff/program/cycles/{cycle_id}")

    assert cycle_page.status_code == 200
    assert "data-deadline-endpoint" not in cycle_page.text
    assert "Приём до 28.09.2026 в 09:30" not in cycle_page.text


# ── путь целиком: конструктор → ученик ──────────────────────────────────────

def test_teacher_sets_deadline_student_submits_then_it_closes(
    client, db, user_factory, session_factory, regular_user
):
    """Весь путь целиком: конструктор → сдача → сдвиг срока → отказ.

    Точечные тесты выше проверяют по звену, а ломается обычно стык: срок,
    сохранённый формой преподавателя, должен доехать до проверки в кабинете
    ученика тем же значением. Здесь же видно главное требование 27.09.2026 —
    после срока ученик не может загрузить, но свою работу и описание видит.
    """
    staff = user_factory(vk_id=701_777, name="Лиза", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(staff).id)
    day = TODAY.isoformat()
    resp = client.post(
        f"/cabinet/staff/program/{day}/material",
        json={
            "title": "Эскиз третьего цикла",
            "audience": {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
            "blocks": [{
                "block_type": BLOCK_PHOTO_UPLOAD,
                "title": "Пришлите эскиз",
                "submit_until": "2026-09-27T09:30",
                "submit_deadlines": [{"tariff": "Я САМ", "submit_until": "2026-09-29T21:00"}],
            }, {
                # Второй шаг держит задание открытым: выполненное задание
                # запирает сданную работу (02.10.2026), а здесь проверяется срок.
                "block_type": BLOCK_PHOTO_UPLOAD,
                "title": "Пришлите второй эскиз",
            }],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert resp.status_code == 200, resp.text
    task = db.query(TrackerTask).filter(TrackerTask.title == "Эскиз третьего цикла").one()
    block = db.query(TaskBlock).filter(
        TaskBlock.task_id == task.id, TaskBlock.title == "Пришлите эскиз"
    ).one()
    assert (block.submit_until.hour, block.submit_until.minute) == (6, 30)

    block.submit_until = day_bounds(TODAY + timedelta(days=1))[0]
    db.commit()
    client.cookies.set("session_id", session_factory(regular_user).id)
    with patch.object(s3_service, "upload_to_s3", return_value=FAKE_URL):
        up = client.post(
            f"/cabinet/tracker/blocks/{block.id}/upload",
            files=[("photos", ("work.jpg", b"bytes", "image/jpeg"))],
            data={"comment": "Мой эскиз"},
        )
    assert up.status_code == 200, up.text
    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]
    assert payload["submit_deadline"]
    assert payload["edit_reason"] is None

    client.cookies.set("session_id", session_factory(staff).id)
    moved = client.post(
        f"/cabinet/staff/program/blocks/{block.id}/deadline",
        json={"submit_until": (TODAY - timedelta(days=1)).isoformat() + "T09:30",
              "submit_deadlines": []},
        headers={"X-CSRF-Token": "x"},
    )
    assert moved.status_code == 200, moved.text

    client.cookies.set("session_id", session_factory(regular_user).id)
    with patch.object(s3_service, "upload_to_s3", return_value=FAKE_URL):
        again = client.post(
            f"/cabinet/tracker/blocks/{block.id}/upload",
            files=[("photos", ("work2.jpg", b"bytes", "image/jpeg"))],
            data={"comment": ""},
        )
    assert again.status_code == 409, again.text
    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]
    assert payload["edit_reason"]
    assert payload["submitted_files"]
    assert payload["submitted_comment"] == "Мой эскиз"

    page = client.get("/cabinet/learning")
    assert page.status_code == 200


# ── срок задания: от конструктора до статистики ─────────────────────────────

def test_task_deadline_reaches_every_block(client, db, user_factory, session_factory, regular_user):
    """Срок задания доезжает до каждого блока и ведёт себя по-разному.

    Один путь проверяет сразу три решения 27.09.2026: срок ставится на задание
    целиком, блок без своего живёт по нему, и у блока без сдачи срок ничего не
    запрещает — только помечает опоздание в статистике.
    """
    staff = user_factory(vk_id=703_001, name="Лиза", is_admin=True, role_name="админ")
    client.cookies.set("session_id", session_factory(staff).id)

    # 1. Преподаватель ставит срок на задание целиком, блокам своего не даёт.
    resp = client.post(
        f"/cabinet/staff/program/{TODAY.isoformat()}/material",
        json={
            "title": "Задание со сроком задания",
            "audience": EVERYONE,
            "submit_until": "2026-09-27T09:30",
            "submit_deadlines": [],
            "blocks": [
                {"block_type": "photo_upload", "title": "Сдать работу"},
                {"block_type": "photo", "title": "Пример", "images": [
                    {"url": "https://s3.example.com/a.jpg", "path": "p/a.jpg"}]},
            ],
        },
        headers={"X-CSRF-Token": "x"},
    )
    assert resp.status_code == 200, resp.text
    task = db.query(TrackerTask).filter(TrackerTask.title == "Задание со сроком задания").one()
    assert (task.submit_until.hour, task.submit_until.minute) == (6, 30)

    # 2. Ученик видит срок у ОБОИХ блоков — он приехал с задания.
    client.cookies.set("session_id", session_factory(regular_user).id)
    blocks = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"]
    assert all(b["submit_deadline"] for b in blocks), [b.get("submit_deadline") for b in blocks]

    # 3. Срок в прошлом: сдача закрыта, а фото-блок отметить всё ещё можно.
    task.submit_until = day_bounds(TODAY - timedelta(days=1))[0]
    db.commit()
    blocks = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"]
    upload = [b for b in blocks if b["block_type"] == "photo_upload"][0]
    photo = [b for b in blocks if b["block_type"] == "photo"][0]
    assert upload["edit_reason"], "сдача должна быть закрыта"
    done = client.post(photo["confirm_endpoint"])
    assert done.status_code == 200, done.text

    # 4. Статистика записала это опозданием.
    stats = get_deadline_stats(db)
    assert stats["late"] >= 1, stats
