"""Сквозная матрица «Доступности блока»: каждая настройка решает одинаково
во всех путях, которыми содержимое блока доходит до ученика.

Владелец 11.10.2026, после двух прод-казусов за день: «то, что отмечено в
настройках задания в разделе „Доступность блока“, работает везде». Аудит
(`plans/2026-10-11-аудит-доступности-блока.md`) нашёл две дыры:

- маршруты записи по номеру блока (`/watched`, `/done`, `/start`, приём
  ответов) проверяли только доступ к заданию, а не к самому блоку — блок
  «только УВЕРЕННЫЙ МАКСИМУМ» общего задания отмечался учеником другого
  тарифа ручным запросом;
- «Откроется не раньше» и дата задания запирали только шаг в разметке
  ленты: JSON задания отдавал содержимое, каталог открывал ролик, запись
  принималась. Подсказка конструктора при этом обещает «раньше ученик блок
  не увидит».

Пути: каталог (`list_published_videos`), прямая ссылка на ролик
(`get_published_video` — через неё `/embed`, `/player-url`, `/progress`),
JSON задания (`/tracker/tasks/{id}/blocks`), запись в блок.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import TARIFF_CONFIDENT_MAX, TARIFF_WITH_YOU
from app.models.learning_video import LearningVideo
from app.models.task_block import (
    BLOCK_PHOTO, BLOCK_QUESTION, BLOCK_TEXT, BLOCK_TIMED, BLOCK_VIDEO, QUESTION_TEXT,
    TaskBlock, TaskBlockImage, TaskBlockLevel, TaskBlockState, TaskBlockStudent,
    TaskBlockTariff,
)
from app.services.tracker import create_task
from app.services.video_catalog import get_published_video, list_published_videos

CASES = ("tariff", "level", "student", "block_date", "task_date")


@pytest.fixture(autouse=True)
def _bunny_enabled(monkeypatch):
    monkeypatch.setattr("app.services.video_catalog.settings.bunny_stream_enabled", True)


def _setup(db, user_factory, case: str | None):
    """Общее задание: открытый всем текст и четыре блока под настройкой
    `case` — видео, фото, контрольная на время, вопрос."""
    staff = user_factory(vk_id=780_001, name="ГП", is_admin=True, role_name="админ")
    other = user_factory(vk_id=780_002, name="Другой", role_name="ученик", tariff=TARIFF_WITH_YOU)
    student = user_factory(vk_id=780_003, name="Ученик", role_name="ученик", tariff=TARIFF_WITH_YOU)
    video = LearningVideo(
        bunny_library_id=1, bunny_video_id="matrix-v1", title="Урок",
        status="ready", is_published=True,
    )
    db.add(video)
    task = create_task(
        db, title="Задание", user_id=staff.id, kind="material",
        assign_to_all=True, is_required=False,
    )
    task.is_published = True
    db.flush()
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    if case == "task_date":
        task.starts_at = tomorrow
    db.add(TaskBlock(task_id=task.id, sort_order=0, block_type=BLOCK_TEXT, body="Всем"))
    restricted = [
        TaskBlock(task_id=task.id, sort_order=1, block_type=BLOCK_VIDEO, video_id=video.id),
        TaskBlock(task_id=task.id, sort_order=2, block_type=BLOCK_PHOTO, title="Фото"),
        TaskBlock(task_id=task.id, sort_order=3, block_type=BLOCK_TIMED, body="Условие"),
        TaskBlock(
            task_id=task.id, sort_order=4, block_type=BLOCK_QUESTION,
            question_type=QUESTION_TEXT, body="Вопрос",
        ),
    ]
    db.add_all(restricted)
    db.flush()
    db.add(TaskBlockImage(block_id=restricted[1].id, image_s3_url="https://s3/secret.jpg"))
    for block in restricted:
        if case == "tariff":
            db.add(TaskBlockTariff(block_id=block.id, tariff=TARIFF_CONFIDENT_MAX))
        elif case == "level":
            db.add(TaskBlockLevel(block_id=block.id, level=2))
        elif case == "student":
            db.add(TaskBlockStudent(block_id=block.id, user_id=other.id))
        elif case == "block_date":
            block.opens_at = tomorrow
    db.commit()
    return student, task, video, restricted


def _viewer(student) -> dict:
    return {"user_id": student.id, "role_rank": 1, "tariff": student.tariff}


def _login(client, session_factory, student):
    client.cookies.set("session_id", session_factory(student).id)


def _done(db, block, student) -> bool:
    state = (
        db.query(TaskBlockState)
        .filter(TaskBlockState.block_id == block.id, TaskBlockState.user_id == student.id)
        .first()
    )
    return state is not None and state.status == "done"


# ── каталог и прямая ссылка ────────────────────────────────────────────────

@pytest.mark.parametrize("case", CASES)
def test_catalog_and_direct_link_hide_the_video_of_a_closed_block(db, user_factory, case):
    student, _task, video, _blocks = _setup(db, user_factory, case)

    assert video.id not in {v.id for v in list_published_videos(db, viewer=_viewer(student))}
    assert get_published_video(db, video.id, viewer=_viewer(student)) is None


def test_catalog_and_direct_link_open_the_video_of_an_open_block(db, user_factory):
    student, _task, video, _blocks = _setup(db, user_factory, None)

    assert video.id in {v.id for v in list_published_videos(db, viewer=_viewer(student))}
    assert get_published_video(db, video.id, viewer=_viewer(student)) is not None


def test_done_block_keeps_its_video_after_the_date_moves_ahead(db, user_factory):
    """Сделанный шаг открыт всегда — как в ленте (`status=done` идёт первым):
    преподаватель передвинул дату вперёд, а ученик урок уже посмотрел."""
    student, _task, video, blocks = _setup(db, user_factory, "block_date")
    db.add(TaskBlockState(block_id=blocks[0].id, user_id=student.id, status="done"))
    db.commit()

    assert get_published_video(db, video.id, viewer=_viewer(student)) is not None


# ── JSON задания ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", CASES)
def test_task_json_carries_no_closed_block(client, db, user_factory, session_factory, case):
    student, task, _video, blocks = _setup(db, user_factory, case)
    _login(client, session_factory, student)

    resp = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks")

    assert resp.status_code == 200, resp.text
    sent = {b["id"] for b in resp.json()["blocks"]}
    assert not sent & {b.id for b in blocks}
    assert "secret.jpg" not in resp.text


def test_task_json_carries_open_blocks(client, db, user_factory, session_factory):
    student, task, _video, blocks = _setup(db, user_factory, None)
    _login(client, session_factory, student)

    sent = {b["id"] for b in client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"]}

    assert {b.id for b in blocks} <= sent


# ── запись в блок ──────────────────────────────────────────────────────────

def _writes(client, task, blocks):
    video, photo, timed, question = blocks
    return {
        "watched": client.post(f"/cabinet/tracker/blocks/{video.id}/watched"),
        "done": client.post(f"/cabinet/tracker/blocks/{photo.id}/done"),
        "start": client.post(f"/cabinet/tracker/blocks/{timed.id}/start"),
        "answer": client.post(
            f"/cabinet/tracker/tasks/{task.id}/blocks",
            json={"answers": [{"block_id": question.id, "text": "Ответ"}]},
        ),
    }


@pytest.mark.parametrize("case", CASES)
def test_closed_block_takes_no_write(client, db, user_factory, session_factory, case):
    student, task, _video, blocks = _setup(db, user_factory, case)
    _login(client, session_factory, student)

    codes = {name: resp.status_code for name, resp in _writes(client, task, blocks).items()}

    assert all(code in (403, 404, 422) for code in codes.values()), codes
    db.expire_all()
    assert not any(_done(db, block, student) for block in blocks)


def test_open_block_takes_writes(client, db, user_factory, session_factory):
    student, task, _video, blocks = _setup(db, user_factory, None)
    _login(client, session_factory, student)

    codes = {name: resp.status_code for name, resp in _writes(client, task, blocks).items()}

    assert codes == {"watched": 200, "done": 200, "start": 200, "answer": 200}, codes


def test_audience_refusal_does_not_reveal_the_block(client, db, user_factory, session_factory):
    """Блок чужого тарифа для ученика не существует — 404, как у задания
    (`_accessible_task_or_404`), а не «закрыто»: ответ не подтверждает, что
    такой блок есть."""
    student, _task, _video, blocks = _setup(db, user_factory, "tariff")
    _login(client, session_factory, student)

    assert client.post(f"/cabinet/tracker/blocks/{blocks[1].id}/done").status_code == 404


def test_date_refusal_says_when(client, db, user_factory, session_factory):
    """Блок ещё не открылся — 403 со словами, а не тихий отказ: ученик видит
    этот шаг в ленте с «Откроется …»."""
    student, _task, _video, blocks = _setup(db, user_factory, "block_date")
    _login(client, session_factory, student)

    # `csrfFetch` ставит этот заголовок сам: без него обработчик 403 рисует
    # HTML-страницу «Нет доступа» (`main.py`).
    resp = client.post(
        f"/cabinet/tracker/blocks/{blocks[1].id}/done", headers={"Accept": "application/json"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Этот шаг ещё не открылся"


# ── «вперёд нельзя» ────────────────────────────────────────────────────────

def test_video_of_a_cycle_locked_by_debt_is_hidden_until_the_debt_is_closed(db, regular_user):
    """Владелец 11.10.2026 («прячь»): ролик цикла, запертого долгом, каталог и
    прямая ссылка не отдают, как не отдают его лента и задание
    (`_accessible_task_or_404`). Прод: 68 учеников, не закрывших «1 неделю»,
    видели в «Видеоуроках» ролики «2 недели». Закрыл долг — ролик открылся."""
    from tests.test_cycle_lock import _program

    from app.services.tracker import close_task_for_user

    _stage, (_c2, _c3, _c4), (t2, t3, t4) = _program(db, regular_user)
    video = LearningVideo(
        bunny_library_id=1, bunny_video_id="matrix-lock", title="Урок цикла 4",
        status="ready", is_published=True,
    )
    db.add(video)
    db.flush()
    db.add(TaskBlock(task_id=t4.id, sort_order=0, block_type=BLOCK_VIDEO, video_id=video.id))
    db.commit()
    viewer = _viewer(regular_user)

    assert video.id not in {v.id for v in list_published_videos(db, viewer=viewer)}
    assert get_published_video(db, video.id, viewer=viewer) is None

    for task in (t2, t3):
        close_task_for_user(db, task, regular_user.id, source="manual")
    db.commit()

    assert video.id in {v.id for v in list_published_videos(db, viewer=viewer)}
    assert get_published_video(db, video.id, viewer=viewer) is not None
