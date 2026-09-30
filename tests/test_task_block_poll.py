"""Опрос — несколько вопросов в одном блоке (владелец 30.09.2026).

Созвон 30.09.2026: «у нас есть заговорка, описание, и пошли вопросы… одиночный
ответ, либо несколько вариантов, либо свободный текст… кнопку далее. И всё
это в одном блоке». Плюс тип ответа «Шкала» из «Шкалы навыков» и вопрос без
верного варианта как голосование.

В конструкторе опрос — одна строка `block_type="poll"`, в базе — подряд
идущие блоки `question`/`scale` с общим `poll_key`
(`api/cabinet_program.py::_expand_polls` / `_fold_polls`).
"""
from datetime import date, timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import (
    BLOCK_QUESTION, BLOCK_SCALE, BLOCK_TEXT, BLOCK_TYPE_LABELS, TaskBlock,
)
from app.models.tracker import TrackerTask
from app.services.cycle_feed import build_cycle_feed
from app.services.program import day_bounds
from app.services.skills_history import skills_history
from app.services.task_blocks import get_options, sync_blocks
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

PROGRAM = "/cabinet/staff/program"
EVERYONE = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=1)
CYCLE_END = TODAY + timedelta(days=6)


def _poll(*questions, title="Как прошла контрольная", body="Ответь честно", **extra):
    return {"block_type": "poll", "title": title, "body": body,
            "questions": list(questions), **extra}


def _single(text, options, correct=None):
    return {"text": text, "question_type": "single",
            "options": [{"text": o, "is_correct": o == correct} for o in options]}


def _text(text):
    return {"text": text, "question_type": "text", "options": []}


def _scale(text, items):
    return {"text": text, "question_type": "scale",
            "options": [{"text": i, "scale_min_label": "легко", "scale_max_label": "сложно"} for i in items]}


def _staff(client, user_factory, session_factory, monkeypatch):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", lambda: date.today())
    monkeypatch.setattr("app.services.program.today_msk", lambda: date.today())
    user = user_factory(vk_id=560_100, name="ГП", is_admin=True,
                        is_group_member=False, role_name="админ")
    client.cookies.set("session_id", session_factory(user).id)
    return user


def _day():
    return (date.today() + timedelta(days=3)).isoformat()


def _blocks(db, task_id):
    return (db.query(TaskBlock).filter(TaskBlock.task_id == task_id)
            .order_by(TaskBlock.sort_order).all())


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    db.add(LearningTopic(
        title="Цикл", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    ))
    db.commit()


def _student_task(db, owner, items):
    """Задание ленты с блоками, собранными тем же разворачиванием опроса, что
    и в конструкторе."""
    from app.api.cabinet_program import _expand_polls

    task = create_task(
        db, title="Анкета", user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY)[1] - timedelta(minutes=1),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.flush()
    sync_blocks(db, task_id=task.id, items=_expand_polls(items))
    db.commit()
    return task


def _statuses(db, user):
    steps = build_cycle_feed(db, user_id=user.id, user_tariff=user.tariff,
                             start=CYCLE_START, end=CYCLE_END)
    return [step["status"] for step in steps]


# ── Конструктор ─────────────────────────────────────────────────────────────


def test_button_is_called_opros():
    assert BLOCK_TYPE_LABELS[BLOCK_QUESTION] == "Опрос"


def test_poll_row_becomes_question_blocks_with_one_key(client, db, user_factory, session_factory, monkeypatch):
    _staff(client, user_factory, session_factory, monkeypatch)
    resp = client.post(f"{PROGRAM}/{_day()}/material", json={
        "title": "Анкета", "audience": EVERYONE,
        "blocks": [_poll(
            _single("Успел за час?", ["Да", "Нет"]),
            _text("Что было сложнее всего?"),
            _scale("Оцени", ["Сложность"]),
            is_required=True,
        )],
    })
    assert resp.status_code == 200, resp.text

    task = db.query(TrackerTask).filter(TrackerTask.kind == "material").one()
    blocks = _blocks(db, task.id)
    assert [b.block_type for b in blocks] == [BLOCK_QUESTION, BLOCK_QUESTION, BLOCK_SCALE]
    assert [b.question_type for b in blocks] == ["single", "text", None]
    keys = {b.poll_key for b in blocks}
    assert len(keys) == 1 and None not in keys
    # Название и описание — на первом блоке, доступность — на каждом.
    assert [b.title for b in blocks] == ["Как прошла контрольная", None, None]
    assert [b.poll_intro for b in blocks] == ["Ответь честно", None, None]
    assert all(b.is_required for b in blocks)
    scale_option = get_options(db, [blocks[2].id])[blocks[2].id][0]
    assert (scale_option.text, scale_option.scale_max_label) == ("Сложность", "сложно")


def test_poll_opens_as_one_row_and_resave_keeps_ids_and_key(client, db, user_factory, session_factory, monkeypatch):
    from app.api.cabinet_program import _edit_payloads

    _staff(client, user_factory, session_factory, monkeypatch)
    client.post(f"{PROGRAM}/{_day()}/material", json={
        "title": "Анкета", "audience": EVERYONE,
        "blocks": [{"block_type": BLOCK_TEXT, "body": "До"},
                   _poll(_single("А?", ["1", "2"]), _scale("Б?", ["X"])),
                   {"block_type": BLOCK_TEXT, "body": "После"}],
    })
    task = db.query(TrackerTask).filter(TrackerTask.kind == "material").one()
    before = _blocks(db, task.id)

    rows = _edit_payloads(db, [task], {})[task.id]["blocks"]
    assert [r["block_type"] for r in rows] == [BLOCK_TEXT, "poll", BLOCK_TEXT]
    poll = rows[1]
    assert poll["title"] == "Как прошла контрольная"
    assert poll["body"] == "Ответь честно"
    assert [q["question_type"] for q in poll["questions"]] == ["single", "scale"]
    assert [q["id"] for q in poll["questions"]] == [before[1].id, before[2].id]

    poll["questions"][0]["text"] = "А, исправленный?"
    resp = client.post(f"{PROGRAM}/items/{task.id}/material",
                       json={"title": "Анкета", "audience": EVERYONE, "blocks": rows})
    assert resp.status_code == 200, resp.text

    db.expire_all()
    after = _blocks(db, task.id)
    assert [b.id for b in after] == [b.id for b in before]
    assert after[1].body == "А, исправленный?"
    assert after[1].poll_key == before[1].poll_key == after[2].poll_key


def test_old_single_question_opens_as_poll_and_gets_key_on_save(client, db, user_factory, session_factory, monkeypatch):
    from app.api.cabinet_program import _edit_payloads

    staff = _staff(client, user_factory, session_factory, monkeypatch)
    task = create_task(db, title="Старое", user_id=staff.id, kind="material",
                       due_at=day_bounds(TODAY)[1], assign_to_all=True)
    db.flush()
    old = sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_QUESTION, "title": "Вопрос дня", "body": "Как дела?", "question_type": "text"},
    ])[0]
    db.commit()

    rows = _edit_payloads(db, [task], {})[task.id]["blocks"]
    assert rows[0]["block_type"] == "poll"
    assert rows[0]["poll_key"] is None
    assert rows[0]["title"] == "Вопрос дня"
    assert rows[0]["questions"] == [
        {"id": old.id, "text": "Как дела?", "question_type": "text", "options": []},
    ]

    from app.api.cabinet_program import sync_task_blocks
    from app.api.cabinet_program import BlockItem
    sync_task_blocks(db, task_id=task.id,
                     items=[BlockItem(**row).model_dump() for row in rows])
    db.commit()
    db.expire_all()
    [block] = _blocks(db, task.id)
    assert block.id == old.id
    assert block.poll_key


def test_two_rows_with_the_same_key_become_two_polls():
    from app.api.cabinet_program import _expand_polls

    items = _expand_polls([
        _poll(_text("1?"), poll_key="abc"),
        _poll(_text("2?"), poll_key="abc"),
    ])
    assert items[0]["poll_key"] == "abc"
    assert items[1]["poll_key"] not in (None, "abc")


def test_poll_validation(client, db, user_factory, session_factory, monkeypatch):
    _staff(client, user_factory, session_factory, monkeypatch)

    def save(poll):
        return client.post(f"{PROGRAM}/{_day()}/material",
                           json={"title": "Анкета", "audience": EVERYONE, "blocks": [poll]})

    assert save(_poll()).status_code == 422
    assert save(_poll(_single("Один вариант?", ["Да"]))).status_code == 422
    assert save(_poll({"text": "Шкала?", "question_type": "scale", "options": []})).status_code == 422
    assert save(_poll(_single("Два верных?", ["А", "Б"], correct=None) | {
        "options": [{"text": "А", "is_correct": True}, {"text": "Б", "is_correct": True}]
    })).status_code == 422
    assert save(_poll({"text": "  ", "question_type": "text", "options": []})).status_code == 422
    # Без верного варианта — голосование, сохраняется.
    assert save(_poll(_single("Голосуем?", ["А", "Б"]))).status_code == 200
    assert db.query(TaskBlock).count() == 1


# ── Ученик ──────────────────────────────────────────────────────────────────


def test_student_gets_poll_key_and_intro(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _student_task(db, user, [_poll(_text("1?"), _text("2?"), body="**Важно**")])

    blocks = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"]

    assert blocks[0]["poll_key"] == blocks[1]["poll_key"]
    assert "<strong>Важно</strong>" in blocks[0]["poll_intro_html"]
    assert "poll_intro_html" not in blocks[1]


def test_first_poll_submission_must_answer_every_question(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _student_task(db, user, [_poll(_text("1?"), _text("2?"))])
    first, second = _blocks(db, task.id)
    url = f"/cabinet/tracker/tasks/{task.id}/blocks"

    partial = client.post(url, json={"answers": [{"block_id": first.id, "text": "Да"}]})
    assert partial.status_code == 422
    empty = client.post(url, json={"answers": [
        {"block_id": first.id, "text": "Да"}, {"block_id": second.id, "text": "  "},
    ]})
    assert empty.status_code == 422

    full = client.post(url, json={"answers": [
        {"block_id": first.id, "text": "Да"}, {"block_id": second.id, "text": "Нет"},
    ]})
    assert full.status_code == 200, full.text
    # После первой отправки текст можно поправить по одному вопросу.
    fix = client.post(url, json={"answers": [{"block_id": second.id, "text": "Уже да"}]})
    assert fix.status_code == 200, fix.text


def test_poll_question_without_right_answer_is_not_graded(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _student_task(db, user, [_poll(_single("Голосуем?", ["А", "Б"]))])
    [block] = _blocks(db, task.id)
    option = get_options(db, [block.id])[block.id][0]

    resp = client.post(f"/cabinet/tracker/tasks/{task.id}/blocks",
                       json={"answers": [{"block_id": block.id, "option_ids": [option.id]}]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["gradable_count"] == 0
    after = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]
    assert after["edit_reason"] == "Ответ сохранён."


def test_only_the_last_poll_question_holds_the_feed(auth_client, db):
    """Вопросы опроса листаются одним мастером — второй не должен ждать
    первого. Хвост ленты опрос держит до полной отправки."""
    client, user = auth_client
    _cycle(db, user)
    task = _student_task(db, user, [
        _poll(_text("1?"), _text("2?"), is_required=True),
        {"block_type": BLOCK_TEXT, "body": "Контрольная 2"},
    ])
    first, second, _ = _blocks(db, task.id)

    assert _statuses(db, user) == ["current", "current", "locked"]

    client.post(f"/cabinet/tracker/tasks/{task.id}/blocks", json={"answers": [
        {"block_id": first.id, "text": "Да"}, {"block_id": second.id, "text": "Нет"},
    ]})

    assert _statuses(db, user) == ["done", "done", "current"]


def test_poll_scale_stays_out_of_skills_history(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _student_task(db, user, [
        _poll(_scale("Сложность?", ["Контрольная"])),
        {"block_type": BLOCK_SCALE, "title": "Навыки", "body": "Оцени себя",
         "options": [{"text": "Композиция"}]},
    ])
    poll_scale, skills = _blocks(db, task.id)
    poll_option = get_options(db, [poll_scale.id])[poll_scale.id][0]
    skill_option = get_options(db, [skills.id])[skills.id][0]

    resp = client.post(f"/cabinet/tracker/tasks/{task.id}/blocks", json={"answers": [
        {"block_id": poll_scale.id, "option_ids": [poll_option.id],
         "option_texts": {str(poll_option.id): "8"}},
        {"block_id": skills.id, "option_ids": [skill_option.id],
         "option_texts": {str(skill_option.id): "5"}},
    ]})
    assert resp.status_code == 200, resp.text

    assert [row["skill"] for row in skills_history(db, user.id)] == ["Композиция"]


def test_reviewer_sees_scale_scores(auth_client, db):
    """В проверке ответов у шкалы видна оценка, а не одно название пункта."""
    from app.services.task_blocks import review_queue

    client, user = auth_client
    _cycle(db, user)
    task = _student_task(db, user, [_poll(_scale("Сложность?", ["Контрольная"]))])
    [block] = _blocks(db, task.id)
    option = get_options(db, [block.id])[block.id][0]
    client.post(f"/cabinet/tracker/tasks/{task.id}/blocks", json={"answers": [
        {"block_id": block.id, "option_ids": [option.id], "option_texts": {str(option.id): "7"}},
    ]})

    [item] = review_queue(db, student_id=user.id)

    assert item["chosen"] == ["Контрольная — 7"]


def test_poll_counts_as_one_step_in_the_progress(db, regular_user):
    """Опрос — одна карточка ленты, значит и один шаг в «Сделано N из M»."""
    from app.api.cabinet_program import _expand_polls
    from app.services.cycle_feed import feed_for_student

    topic = LearningTopic(
        title="Цикл", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=regular_user.id,
    )
    db.add(topic)
    db.commit()
    task = create_task(db, title="Анкета", user_id=regular_user.id, kind="material",
                       topic_id=topic.id, assign_to_all=True, is_required=True)
    task.is_published = True
    db.flush()
    sync_blocks(db, task_id=task.id, items=_expand_polls([
        _poll(_text("1?"), _text("2?"), _text("3?")),
        {"block_type": BLOCK_TEXT, "body": "Дальше"},
    ]))
    db.commit()

    feed = feed_for_student(db, user_id=regular_user.id, user_tariff=regular_user.tariff, today=TODAY)

    assert feed["total_count"] == 2
