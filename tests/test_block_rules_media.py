"""Пункты правил с фото, видео, аудио и текстом (владелец 04.10.2026).

«Нужно дать возможность добавлять в данное задание фото, видео, аудио, текст,
чтобы под ними стоял чек бокс, что пользователь ознакомился с фото, либо
другим контентом и мог проставить согласие.»

Решения владельца: видео — загруженным файлом (не ролик каталога), фото —
галереей до десяти в одном пункте, текст — длинный с разметкой. Галочка
одна на пункт, шаг закрывается только по всем галочкам — как и раньше
(`tests/test_block_rules.py`).
"""
from datetime import date, timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import (
    BLOCK_RULES,
    RULE_ITEM_DEFAULT_LABEL,
    TaskBlock,
    TaskBlockOption,
    TaskBlockOptionImage,
)
from app.services.program import day_bounds
from app.services.task_blocks import (
    get_option_images,
    get_options,
    get_state,
    sync_blocks,
)
from app.services.tracker import copy_task_blocks, create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()
PROGRAM = "/cabinet/staff/program"
EVERYONE = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}

PHOTOS = [
    {"url": "https://s3.example/rules/1.jpg", "path": "rules/1.jpg"},
    {"url": "https://s3.example/rules/2.jpg", "path": "rules/2.jpg"},
]
VIDEO = "https://s3.example/zadaniya-media/note/abc.mp4"
AUDIO = "https://s3.example/zadaniya-media/voice/abc.m4a"


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    db.add(LearningTopic(
        title="Предобучение", opens_at=_utc(msk_midnight(TODAY - timedelta(days=1))),
        ends_at=_utc(msk_midnight(TODAY + timedelta(days=6)) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    ))
    db.commit()


def _task(db, owner, *, title="Правила"):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY + timedelta(days=2))[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _items():
    """По пункту каждого вида, в порядке, который увидит ученик."""
    return [
        {"text": "Ознакомлен с правилами чата",
         "description": "**Не пересылать** материалы\n- даже друзьям"},
        {"text": "", "content_kind": "photo", "images": PHOTOS},
        {"text": "Посмотрел видео", "content_kind": "video",
         "media_url": VIDEO, "media_path": "zadaniya-media/note/abc.mp4"},
        {"text": "Послушал", "content_kind": "audio",
         "media_url": AUDIO, "media_path": "zadaniya-media/voice/abc.m4a"},
    ]


def _rules(db, task, options=None, *, block_id=None):
    blocks = sync_blocks(db, task_id=task.id, items=[{
        "id": block_id,
        "block_type": BLOCK_RULES,
        "title": "Правила школы",
        "is_required": True,
        "options": options if options is not None else _items(),
    }])
    db.commit()
    return blocks[0]


def _options(db, block):
    return get_options(db, [block.id]).get(block.id, [])


# ── сохранение ──────────────────────────────────────────────────────────────

def test_rule_items_of_every_kind_are_saved(db, regular_user):
    task = _task(db, regular_user)
    block = _rules(db, task)

    text, photo, video, audio = _options(db, block)
    assert (text.content_kind, text.description) == (None, "**Не пересылать** материалы\n- даже друзьям")
    # Подпись у фото не вписали — галочка получает подпись по умолчанию.
    assert (photo.content_kind, photo.text) == ("photo", RULE_ITEM_DEFAULT_LABEL)
    assert [i.image_s3_url for i in get_option_images(db, [photo.id])[photo.id]] == [
        p["url"] for p in PHOTOS
    ]
    assert (video.content_kind, video.media_s3_url) == ("video", VIDEO)
    assert (audio.content_kind, audio.media_s3_url) == ("audio", AUDIO)


def test_resave_by_id_keeps_files(db, regular_user):
    """Повторное сохранение открытого задания не теряет фото и записи."""
    task = _task(db, regular_user)
    block = _rules(db, task)
    saved = _options(db, block)
    items = _items()
    for item, option in zip(items, saved):
        item["id"] = option.id

    block = _rules(db, task, items, block_id=block.id)

    again = _options(db, block)
    assert [o.id for o in again] == [o.id for o in saved]
    assert len(get_option_images(db, [again[1].id])[again[1].id]) == 2
    assert again[2].media_s3_url == VIDEO


def test_media_item_without_file_is_dropped(db, regular_user):
    """Нажали «+ Фото» и ничего не загрузили — пункт пустой, его нет."""
    task = _task(db, regular_user)
    block = _rules(db, task, [
        {"text": "Правило", "description": "Текст"},
        {"text": "Ознакомлен(а)", "content_kind": "photo", "images": []},
        {"text": "Ознакомлен(а)", "content_kind": "video", "media_url": ""},
    ])

    assert [o.content_kind for o in _options(db, block)] == [None]


def test_rules_with_only_empty_media_items_are_dropped(db, regular_user):
    task = _task(db, regular_user)
    blocks = sync_blocks(db, task_id=task.id, items=[{
        "block_type": BLOCK_RULES,
        "options": [{"text": "", "content_kind": "photo", "images": []}],
    }])
    db.commit()

    assert blocks == []


def test_removed_item_takes_its_photos(db, regular_user):
    task = _task(db, regular_user)
    block = _rules(db, task)
    text = _options(db, block)[0]

    _rules(db, task, [{"id": text.id, "text": text.text, "description": text.description}],
           block_id=block.id)

    assert db.query(TaskBlockOptionImage).count() == 0
    assert db.query(TaskBlockOption).count() == 1


def test_switched_kind_drops_foreign_fields(db, regular_user):
    """Пункт переключили с видео на текст — файл видео у него не остаётся."""
    task = _task(db, regular_user)
    block = _rules(db, task)
    video = _options(db, block)[2]

    _rules(db, task, [{
        "id": video.id, "text": "Теперь текст", "description": "Правило",
        "media_url": VIDEO,
    }], block_id=block.id)

    [option] = _options(db, block)
    assert (option.content_kind, option.media_s3_url, option.media_s3_path) == (None, None, None)


def test_other_block_types_ignore_rule_fields(db, regular_user):
    """Поля пункта правил — только у правил: вариант вопроса их не держит."""
    task = _task(db, regular_user)
    [block] = sync_blocks(db, task_id=task.id, items=[{
        "block_type": "question", "body": "Вопрос", "question_type": "single",
        "options": [
            {"text": "Да", "is_correct": True, "content_kind": "photo", "images": PHOTOS},
            {"text": "Нет"},
        ],
    }])
    db.commit()

    assert [o.content_kind for o in _options(db, block)] == [None, None]
    assert db.query(TaskBlockOptionImage).count() == 0


def test_week_copy_carries_rule_media(db, regular_user):
    task = _task(db, regular_user)
    _rules(db, task)
    copy = _task(db, regular_user, title="Копия")

    copy_task_blocks(db, from_task_id=task.id, to_task_id=copy.id)
    db.commit()

    block = db.query(TaskBlock).filter(TaskBlock.task_id == copy.id).one()
    text, photo, video, audio = _options(db, block)
    assert text.description.startswith("**Не пересылать**")
    assert len(get_option_images(db, [photo.id])[photo.id]) == 2
    assert (video.content_kind, video.media_s3_url) == ("video", VIDEO)
    assert audio.media_s3_path == "zadaniya-media/voice/abc.m4a"


# ── ученик ──────────────────────────────────────────────────────────────────

def test_student_sees_item_content(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _rules(db, task)

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    item = next(b for b in payload["blocks"] if b["id"] == block.id)
    text, photo, video, audio = item["options"]

    assert text["kind"] is None
    assert "<strong>Не пересылать</strong>" in text["description_html"]
    assert photo["kind"] == "photo"
    assert [i["url"] for i in photo["images"]] == [p["url"] for p in PHOTOS]
    assert (video["kind"], video["media_url"]) == ("video", VIDEO)
    assert (audio["kind"], audio["media_url"]) == ("audio", AUDIO)
    # Путь в хранилище ученику не нужен.
    assert "media_path" not in video


def test_all_ticks_still_close_the_step(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _rules(db, task)
    ids = [o.id for o in _options(db, block)]

    partial = client.post(f"/cabinet/tracker/tasks/{task.id}/blocks", json={
        "answers": [{"block_id": block.id, "option_ids": ids[:3]}],
    })
    assert partial.status_code == 200
    assert get_state(db, block_id=block.id, user_id=user.id) is None

    full = client.post(f"/cabinet/tracker/tasks/{task.id}/blocks", json={
        "answers": [{"block_id": block.id, "option_ids": ids}],
    })
    assert full.status_code == 200
    assert get_state(db, block_id=block.id, user_id=user.id).status == "done"


# ── конструктор преподавателя ───────────────────────────────────────────────

def _staff(client, user_factory, session_factory):
    user = user_factory(
        vk_id=660_400, name="Главный преподаватель", is_admin=True,
        is_group_member=False, role_name="админ",
    )
    client.cookies.set("session_id", session_factory(user).id)
    return user


def _save_material(client, monkeypatch, options):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: date.today())
    monkeypatch.setattr("app.services.program.today_msk", lambda: date.today())
    day = (date.today() + timedelta(days=3)).isoformat()
    return client.post(f"{PROGRAM}/{day}/material", json={
        "title": "Правила", "audience": EVERYONE,
        "blocks": [{"block_type": BLOCK_RULES, "title": "Правила школы", "options": options}],
    }), day


def test_constructor_saves_media_items(client, db, user_factory, session_factory, monkeypatch):
    _staff(client, user_factory, session_factory)
    items = _items()
    items[1]["text"] = RULE_ITEM_DEFAULT_LABEL

    resp, day = _save_material(client, monkeypatch, items)

    assert resp.status_code == 200, resp.text
    block = db.query(TaskBlock).filter(TaskBlock.block_type == BLOCK_RULES).one()
    assert [o.content_kind for o in _options(db, block)] == [None, "photo", "video", "audio"]
    # Форма правки получает фото и записи обратно: без них повторное
    # сохранение отбило бы фото-пункт как пустой.
    page = client.get(f"{PROGRAM}/{day}")
    assert PHOTOS[0]["url"] in page.text
    assert VIDEO in page.text


def test_constructor_refuses_photo_item_without_photo(client, db, user_factory, session_factory, monkeypatch):
    _staff(client, user_factory, session_factory)

    resp, _day = _save_material(client, monkeypatch, [
        {"text": "Правило", "description": "Текст"},
        {"text": "Ознакомлен(а)", "content_kind": "photo", "images": []},
    ])

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["msg"] == "В пункте правил №2 нет фото"
    assert db.query(TaskBlock).count() == 0


def test_constructor_refuses_video_item_without_file(client, db, user_factory, session_factory, monkeypatch):
    _staff(client, user_factory, session_factory)

    resp, _day = _save_material(client, monkeypatch, [
        {"text": "Ознакомлен(а)", "content_kind": "video"},
    ])

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["msg"] == "В пункте правил №1 нет видео"


def test_constructor_offers_four_rule_item_buttons(admin_client):
    client, _ = admin_client
    page = client.get(f"{PROGRAM}/{(TODAY + timedelta(days=14)).isoformat()}")

    for kind in ("text", "photo", "video", "audio"):
        assert f'data-add-rule-item="{kind}"' in page.text
    assert "data-mrf-attach-only" in page.text
