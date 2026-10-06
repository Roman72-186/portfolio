"""Свой мост у видео-блока (владелец 06.10.2026: «добавь в блоке Видео
переключение моста»).

Зачем: проверить российскую копию моста на настоящем задании, открытом только
владельцу, не переключая всех учеников. Пустое значение — общая настройка
`BUNNY_PLAYER_PROXY_BASE`, так ведут себя все блоки, заведённые до 06.10.
"""
from datetime import timedelta

from app.config import settings
from app.models.learning_video import LearningVideo
from app.models.task_block import BLOCK_VIDEO, TaskBlock
from app.services.program import day_bounds
from app.services.task_blocks import sync_blocks
from app.services.tracker import copy_task_blocks, create_task
from app.services.tz import today_msk

RU = "https://video-ru.assaru.space"
NL = "https://video.assaru.space"


def _configure_bunny(monkeypatch, *, proxy_base=NL):
    monkeypatch.setattr(settings, "bunny_stream_enabled", True)
    monkeypatch.setattr(settings, "bunny_stream_library_id", 720058)
    monkeypatch.setattr(settings, "bunny_stream_token_key", "playback-key")
    monkeypatch.setattr(settings, "bunny_stream_token_ttl_seconds", 300)
    monkeypatch.setattr(settings, "bunny_player_proxy_base", proxy_base)


def _video(db):
    video = LearningVideo(
        bunny_library_id=720058, bunny_video_id="35ed80ae-8103-4528-a700-3f69ec56957d",
        title="Проверка моста", status="ready", is_published=True,
    )
    db.add(video)
    db.commit()
    return video


def _task(db, owner):
    task = create_task(
        db, title="Видео через мост", user_id=owner.id, kind="material",
        due_at=day_bounds(today_msk())[0] + timedelta(hours=6),
        assign_to_all=True, is_required=False,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _block(db, task_id):
    return db.query(TaskBlock).filter(TaskBlock.task_id == task_id).one()


def test_video_block_keeps_its_bridge(db, regular_user):
    task = _task(db, regular_user)
    video = _video(db)

    sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_VIDEO, "video_id": video.id, "video_bridge": "ru"},
    ])
    db.commit()

    assert _block(db, task.id).video_bridge == "ru"


def test_unknown_bridge_and_foreign_block_types_are_dropped(db, regular_user):
    task = _task(db, regular_user)
    video = _video(db)

    sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_VIDEO, "video_id": video.id, "video_bridge": "https://evil.example"},
    ])
    db.commit()
    assert _block(db, task.id).video_bridge is None

    # Мост — только у блока «Видео»: у кнопки портфолио с роликом его нет.
    sync_blocks(db, task_id=task.id, items=[
        {"block_type": "portfolio", "video_id": video.id, "video_bridge": "ru"},
    ])
    db.commit()
    assert _block(db, task.id).video_bridge is None


def test_student_gets_the_block_bridge_in_the_player_address(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    video = _video(db)
    sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_VIDEO, "video_id": video.id, "video_bridge": "ru"},
    ])
    db.commit()

    block = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]

    assert block["video_embed_endpoint"] == f"/cabinet/videos/{video.id}/embed?bridge=ru"


def test_block_without_bridge_keeps_the_plain_address(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    video = _video(db)
    sync_blocks(db, task_id=task.id, items=[{"block_type": BLOCK_VIDEO, "video_id": video.id}])
    db.commit()

    block = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]

    assert block["video_embed_endpoint"] == f"/cabinet/videos/{video.id}/embed"


def test_embed_goes_through_the_chosen_bridge_and_keeps_it_on_refresh(admin_client, db, monkeypatch):
    _configure_bunny(monkeypatch, proxy_base=NL)
    video = _video(db)
    client, _ = admin_client

    body = client.get(f"/cabinet/videos/{video.id}/embed?bridge=ru").json()

    assert body["player_url"].startswith(f"{RU}/embed/")
    assert body["player_js_url"].startswith(RU)
    # Без флага в адресе перевыпуска плеер через пять минут уехал бы на общий мост.
    assert body["player_url_endpoint"] == f"/cabinet/videos/{video.id}/player-url?bridge=ru"
    refreshed = client.get(body["player_url_endpoint"]).json()
    assert refreshed["player_url"].startswith(f"{RU}/embed/")


def test_embed_without_flag_follows_the_global_setting(admin_client, db, monkeypatch):
    _configure_bunny(monkeypatch, proxy_base=NL)
    video = _video(db)
    client, _ = admin_client

    body = client.get(f"/cabinet/videos/{video.id}/embed").json()

    assert body["player_url"].startswith(f"{NL}/embed/")
    assert body["player_url_endpoint"] == f"/cabinet/videos/{video.id}/player-url"


def test_nl_choice_goes_through_the_netherlands_even_when_all_are_on_russia(admin_client, db, monkeypatch):
    _configure_bunny(monkeypatch, proxy_base=RU)
    video = _video(db)
    client, _ = admin_client

    body = client.get(f"/cabinet/videos/{video.id}/embed?bridge=nl").json()

    assert body["player_url"].startswith(f"{NL}/embed/")


def test_copied_task_keeps_the_block_bridge(db, regular_user):
    source = _task(db, regular_user)
    target = _task(db, regular_user)
    video = _video(db)
    sync_blocks(db, task_id=source.id, items=[
        {"block_type": BLOCK_VIDEO, "video_id": video.id, "video_bridge": "ru"},
    ])
    db.commit()

    copy_task_blocks(db, from_task_id=source.id, to_task_id=target.id)
    db.commit()

    assert _block(db, target.id).video_bridge == "ru"
