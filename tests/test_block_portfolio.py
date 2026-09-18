"""Блок «Загрузить портфолио» (владелец 03.09.2026).

«Добавить кнопку "загрузить портфолио" — эта кнопка перенесёт сразу на готовый
наш функционал… только здесь нужно сделать так, что он обязан загрузить это
портфолио: пока не будет подтверждения, что он загрузил портфолио, которое
именно 18 числа, у него не откроется актуальное образовательное пространство
дальше.»

Своего хранилища у блока нет: он ведёт на существующий экран загрузки работ и
закрывается фактом загрузки, а не галочкой ученика.
"""
from datetime import timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import (
    BLOCK_PORTFOLIO,
    BLOCK_TEXT,
    TaskBlock,
    TaskBlockState,
)
from app.models.work import Work
from app.services.cycle_feed import build_cycle_feed, has_portfolio_upload
from app.services.program import day_bounds
from app.services.tracker import create_task
from app.services.tz import msk_midnight, now_msk, today_msk

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=2)
CYCLE_END = TODAY + timedelta(days=12)


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    topic = LearningTopic(
        title="Цикл", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    return topic


def _task(db, owner, *, title="Первый день", due_on=TODAY):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(due_on)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _portfolio_block(db, task, order=1):
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_PORTFOLIO,
        title="Загрузите портфолио", sort_order=order, is_required=True,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _work(db, user, *, days_ago=0, work_type="before", status="success"):
    """Успешная работа «До» по умолчанию — то же, что кладёт боевая загрузка
    (`api/upload.py::_process_uploads` ставит `status="success"` сразу)."""
    work = Work(
        user_id=user.id, work_type=work_type, month="сентябрь", year=TODAY.year,
        filename="work.jpg", s3_url="https://example.com/work.jpg",
        status=status,
    )
    db.add(work)
    db.flush()
    work.created_at = _utc(
        msk_midnight(TODAY - timedelta(days=days_ago)) + timedelta(hours=12)
    )
    db.commit()
    return work


def _feed(db, user):
    return build_cycle_feed(
        db, user_id=user.id, user_tariff=user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )


# ── факт загрузки ───────────────────────────────────────────────────────────

def test_no_upload_means_not_done(db, regular_user):
    assert has_portfolio_upload(db, regular_user.id, since=CYCLE_START) is False


def test_upload_inside_the_period_counts(db, regular_user):
    _work(db, regular_user)

    assert has_portfolio_upload(db, regular_user.id, since=CYCLE_START) is True


def test_old_upload_does_not_count(db, regular_user):
    """«Портфолио, которое именно 18 числа» — прошлогодняя работа не закрывает
    сегодняшний шаг."""
    _work(db, regular_user, days_ago=90)

    assert has_portfolio_upload(db, regular_user.id, since=CYCLE_START) is False


# Тип и статус работы (владелец 09.09.2026: «по этой кнопке работы загружаются
# в ДО»). До этого шаг закрывала любая работа: сданный пробник засчитывался за
# портфолио предобучения, а неудачная загрузка открывала ленту без файла.

def test_after_work_does_not_count(db, regular_user):
    _work(db, regular_user, work_type="after")

    assert has_portfolio_upload(db, regular_user.id, since=CYCLE_START) is False


def test_mock_exam_work_does_not_count(db, regular_user):
    _work(db, regular_user, work_type="mock_exam")

    assert has_portfolio_upload(db, regular_user.id, since=CYCLE_START) is False


def test_failed_upload_does_not_count(db, regular_user):
    _work(db, regular_user, status="failed")

    assert has_portfolio_upload(db, regular_user.id, since=CYCLE_START) is False


# ── поведение в ленте ───────────────────────────────────────────────────────

def test_portfolio_step_is_open_until_the_work_is_uploaded(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    _portfolio_block(db, task)

    steps = _feed(db, regular_user)

    assert steps[0]["status"] == "current"


def test_portfolio_step_closes_itself_after_upload(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    _portfolio_block(db, task)
    _work(db, regular_user)

    steps = _feed(db, regular_user)

    assert steps[0]["status"] == "done"


def test_portfolio_step_blocks_the_rest_of_the_feed(db, regular_user):
    """«Пока не загрузит — у него не откроется образовательное пространство
    дальше»."""
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    _portfolio_block(db, task, order=1)
    db.add(TaskBlock(
        task_id=task.id, block_type=BLOCK_TEXT, title="Правила",
        body="текст правил", sort_order=2, is_required=True,
    ))
    db.commit()

    steps = _feed(db, regular_user)

    assert [s["status"] for s in steps] == ["current", "locked"]


def test_upload_opens_the_rest_of_the_feed(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    _portfolio_block(db, task, order=1)
    db.add(TaskBlock(
        task_id=task.id, block_type=BLOCK_TEXT, title="Правила",
        body="текст правил", sort_order=2, is_required=True,
    ))
    db.commit()
    _work(db, regular_user)

    steps = _feed(db, regular_user)

    assert [s["status"] for s in steps] == ["done", "current"]


def test_expired_personal_window_releases_the_rest_of_the_feed(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    block = _portfolio_block(db, task, order=1)
    block.portfolio_window_hours = 24
    db.add(TaskBlock(
        task_id=task.id, block_type=BLOCK_TEXT, title="Следующий шаг",
        body="Можно продолжать", sort_order=2, is_required=True,
    ))
    db.commit()

    first = _feed(db, regular_user)
    state = db.query(TaskBlockState).filter_by(
        block_id=block.id, user_id=regular_user.id
    ).one()
    state.started_at = _utc(now_msk() - timedelta(hours=25))
    db.commit()

    expired = _feed(db, regular_user)

    assert [s["status"] for s in first] == ["current", "locked"]
    assert [s["status"] for s in expired] == ["locked", "current"]


def test_work_uploaded_before_personal_window_does_not_close_it(db, regular_user):
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    block = _portfolio_block(db, task)
    block.portfolio_window_hours = 72
    db.commit()
    _work(db, regular_user)

    steps = _feed(db, regular_user)

    assert steps[0]["status"] == "current"
    state = db.query(TaskBlockState).filter_by(
        block_id=block.id, user_id=regular_user.id
    ).one()
    assert state.started_at is not None
    assert state.status != "done"


# ── конструктор ─────────────────────────────────────────────────────────────

def test_constructor_offers_the_portfolio_block(admin_client):
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert 'data-add-block="portfolio"' in page.text
    assert "Загрузить портфолио" in page.text


def test_portfolio_block_saves_without_content(admin_client, db):
    """Кнопка самодостаточна: без заголовка и пояснения блок всё равно нужен."""
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    resp = client.post(f"/cabinet/staff/program/{day}/material", json={
        "title": "Первый день",
        "description": None,
        "subject": None,
        "is_required": True,
        "starts_on": None,
        "blocks": [{"block_type": "portfolio", "title": None, "body": None,
                    "is_required": True, "subject": None, "tariffs": [],
                    "opens_at": None, "bypass_sequence": False,
                    "portfolio_window_hours": 72}],
        "audience": {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""},
    })

    assert resp.status_code == 200
    block = db.query(TaskBlock).filter(TaskBlock.block_type == BLOCK_PORTFOLIO).first()
    assert block is not None
    assert block.is_required is True
    assert block.portfolio_window_hours == 72


# ── ссылка кнопки ───────────────────────────────────────────────────────────

def test_button_points_at_the_before_section(auth_client, db):
    """Кнопка ведёт строго в «До», даже когда онбординг уже пройден.

    Владелец 09.09.2026: «по этой кнопке работы загружаются в ДО». Без явного
    `section` экран загрузки выбирает раздел сам по `portfolio_do_completed`,
    и ученик, уже грузивший работы, попадал в «После».
    """
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.commit()
    task = _task(db, user)
    _portfolio_block(db, task)

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()

    # `block` в ссылке (18.09.2026) — подсказка экрану загрузки, какое окно
    # ученик открыл: при двух открытых он покажет срок того, с кнопки которого
    # пришли. Права ссылка не даёт, окно сервер проверяет сам.
    assert payload["blocks"][0]["upload_url"].startswith("/upload?section=before&block=")


# ── инструкция над кнопкой: видео и фото (владелец 17.09.2026) ─────────────
# «В кнопку «Загрузить портфолио» добавить видеоинструкцию, фотографии,
# скриншоты, а ниже — сама кнопка».

def _video(db):
    from app.models.learning_video import LearningVideo

    video = LearningVideo(
        bunny_library_id=1, bunny_video_id="portfolio-howto",
        title="Как загрузить портфолио", status="ready", is_published=True,
    )
    db.add(video)
    db.commit()
    return video


def test_portfolio_block_keeps_video_and_images(db, regular_user):
    from app.services.task_blocks import get_images, sync_blocks

    task = _task(db, regular_user)
    video = _video(db)

    sync_blocks(db, task_id=task.id, items=[{
        "block_type": "portfolio", "video_id": video.id,
        "images": [{"url": "https://example.com/a.jpg", "path": "blocks/a.jpg"},
                   {"url": "https://example.com/b.jpg", "path": "blocks/b.jpg"}],
    }])
    db.commit()

    block = db.query(TaskBlock).filter(TaskBlock.task_id == task.id).one()
    assert block.video_id == video.id
    assert [i.image_s3_url for i in get_images(db, [block.id])[block.id]] == [
        "https://example.com/a.jpg", "https://example.com/b.jpg",
    ]


def test_student_payload_carries_video_and_images_above_button(auth_client, db):
    from app.services.task_blocks import sync_blocks

    client, user = auth_client
    task = _task(db, user)
    video = _video(db)
    sync_blocks(db, task_id=task.id, items=[{
        "block_type": "portfolio", "video_id": video.id,
        "images": [{"url": "https://example.com/a.jpg", "path": None}],
    }])
    db.commit()

    block = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]

    assert block["video_embed_endpoint"] == f"/cabinet/videos/{video.id}/embed"
    assert block["images"] == [{"url": "https://example.com/a.jpg"}]
    assert block["upload_url"].startswith("/upload?section=before&block=")


def test_portfolio_block_without_instruction_stays_a_plain_button(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    _portfolio_block(db, task)

    block = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]

    assert block["video_embed_endpoint"] is None
    assert block["images"] == []


def test_video_in_portfolio_block_follows_block_access(db, user_factory):
    """Ролик в кнопке портфолио — тоже «ролик в блоке»: открыт ровно тем, кому
    открыто задание, а не всей школе по правилу «без темы — всем»."""
    from app.models.tracker import TrackerTask
    from app.services.task_blocks import sync_blocks
    from app.services.video_catalog import block_bound_video_ids, is_video_accessible

    student = user_factory(vk_id=710_901, name="Ученик")
    video = _video(db)
    task = TrackerTask(title="Не для всех", kind="material",
                       is_published=True, assign_to_all=False)
    db.add(task)
    db.flush()
    sync_blocks(db, task_id=task.id,
                items=[{"block_type": "portfolio", "video_id": video.id}])
    db.commit()

    viewer = {"user_id": student.id, "role_rank": 1, "tariff": student.tariff}
    assert video.id in block_bound_video_ids(db)
    assert is_video_accessible(db, video, viewer) is False


def test_constructor_portfolio_block_has_video_and_photo_fields(admin_client):
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert "Без видеоинструкции" in page.text
    assert "Фото и скриншоты, необязательно" in page.text
    assert "Окно загрузки, часов" in page.text
