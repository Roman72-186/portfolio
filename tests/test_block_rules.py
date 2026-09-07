"""Правила школы с галочкой у каждого пункта (владелец 03.09.2026).

«Он должен ознакомиться с правилами по нашему образовательному пространству,
и как бы он должен их прочитать и поставить галочки рядом с этими правилами,
что он с ними ознакомился. И дальше мы хотели, после того, чтобы он попадал в
тест, где мы проверяем его знания и понимания правил.»

До 07.09.2026 это собиралось вопросом с несколькими вариантами, и первый день
предобучения 18 сентября собран из него же. Разница, ради которой заведён
отдельный тип: у вопроса «ответил» значит «отметил хоть что-то», а согласие
считается принятым, только когда отмечены **все** пункты.

Главное, что здесь проверяется — частичная отметка не запирает ленту. Это тот
же класс поломки, что чинился 07.09.2026 у вопросов: стоило записать ответ,
которого недостаточно для закрытия, и форма отправки исчезала вместе с
единственным способом дослать пропущенное.
"""
from datetime import timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import (
    BLOCK_QUESTION,
    BLOCK_RULES,
    BLOCK_TEXT,
    TaskBlock,
    TaskBlockOption,
)
from app.services.cycle_feed import build_cycle_feed
from app.services.program import day_bounds
from app.services.task_blocks import (
    answered_block_ids,
    get_options,
    get_response,
    get_state,
    grade_response,
    question_blocks,
    sync_blocks,
)
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=1)
CYCLE_END = TODAY + timedelta(days=6)

RULES = ("Не делать скриншоты материалов", "Не вести запись экрана", "Не сливать работы")


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    db.add(LearningTopic(
        title="Предобучение", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    ))
    db.commit()


def _task(db, owner, *, title="18 сентября"):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _rules_block(db, task, *, rules=RULES, required=True, tail=True):
    """Блок правил и, по умолчанию, текстовый шаг за ним — чтобы было видно,
    запирает ли незакрытое согласие хвост ленты."""
    items = [{
        "block_type": BLOCK_RULES,
        "title": "Правила школы",
        "body": "Отметьте каждый пункт, с которым ознакомились",
        "is_required": required,
        "options": [{"id": None, "text": text, "is_correct": False} for text in rules],
    }]
    if tail:
        items.append({"block_type": BLOCK_TEXT, "body": "Тест по правилам"})
    blocks = sync_blocks(db, task_id=task.id, items=items)
    db.commit()
    return blocks[0]


def _statuses(db, user):
    steps = build_cycle_feed(
        db, user_id=user.id, user_tariff=user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )
    return [step["status"] for step in steps]


def _answer(client, task_id, answers):
    return client.post(f"/cabinet/tracker/tasks/{task_id}/blocks", json={"answers": answers})


def _option_ids(db, block):
    return [option.id for option in get_options(db, [block.id])[block.id]]


# ── конструктор ─────────────────────────────────────────────────────────────

def test_rules_keep_their_items(db, regular_user):
    task = _task(db, regular_user)
    block = _rules_block(db, task)

    options = db.query(TaskBlockOption).filter(TaskBlockOption.block_id == block.id).all()
    assert [option.text for option in options] == list(RULES)


def test_rules_without_items_are_dropped(db, regular_user):
    """Согласие без единого пункта бессмысленно — как шкала без навыков."""
    task = _task(db, regular_user)
    blocks = sync_blocks(db, task_id=task.id, items=[{
        "block_type": BLOCK_RULES, "title": "Пустые правила", "options": [],
    }])
    db.commit()

    assert blocks == []


def test_rules_are_answerable_blocks(db, regular_user):
    """Правила отвечаются той же формой, что вопросы и шкала."""
    task = _task(db, regular_user)
    block = _rules_block(db, task, tail=False)

    assert [b.id for b in question_blocks([block])] == [block.id]


def test_rules_are_not_graded(db, regular_user):
    """Согласие не бывает верным или неверным: в счёт проверки не идёт."""
    task = _task(db, regular_user)
    block = _rules_block(db, task, tail=False)

    verdict = grade_response(db, blocks=[block], response_id=None)

    assert verdict["gradable_count"] == 0
    assert verdict["results"] == []


# ── ученик ──────────────────────────────────────────────────────────────────

def test_all_ticks_close_the_block_and_open_the_tail(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _rules_block(db, task)

    assert _statuses(db, user) == ["current", "locked"]

    resp = _answer(client, task.id, [
        {"block_id": block.id, "option_ids": _option_ids(db, block)}
    ])

    assert resp.status_code == 200
    assert _statuses(db, user) == ["done", "current"]


def test_partial_ticks_leave_the_step_open(auth_client, db):
    """Отметил не всё — шаг не закрылся, хвост по-прежнему закрыт."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _rules_block(db, task)

    resp = _answer(client, task.id, [
        {"block_id": block.id, "option_ids": _option_ids(db, block)[:2]}
    ])

    assert resp.status_code == 200
    assert get_state(db, block_id=block.id, user_id=user.id) is None
    assert _statuses(db, user) == ["current", "locked"]


def test_partial_ticks_do_not_count_as_answered(auth_client, db):
    """Ключевая развилка: недоотмеченное согласие не должно считаться ответом.

    Иначе `answered_block_ids` вернёт этот блок, форма отправки исчезнет — и
    обязательное согласие запрёт ленту навсегда, потому что дослать галочки
    станет нечем. Ровно эта поломка чинилась 07.09.2026 у вопросов.
    """
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _rules_block(db, task)

    _answer(client, task.id, [
        {"block_id": block.id, "option_ids": _option_ids(db, block)[:1]}
    ])

    response = get_response(db, task_id=task.id, user_id=user.id)
    answered = (
        answered_block_ids(db, response_id=response.id) if response else set()
    )
    assert block.id not in answered


def test_student_can_finish_ticking_later(auth_client, db):
    """Дослал недостающие галочки — шаг закрылся."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _rules_block(db, task)
    options = _option_ids(db, block)

    _answer(client, task.id, [{"block_id": block.id, "option_ids": options[:2]}])
    _answer(client, task.id, [{"block_id": block.id, "option_ids": options}])

    assert _statuses(db, user) == ["done", "current"]


def test_foreign_option_does_not_close_the_block(auth_client, db):
    """Галочка из чужого блока не считается — как и у вопросов (07.09.2026)."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _rules_block(db, task, tail=False)
    other = _rules_block(db, _task(db, user, title="Соседний день"), tail=False)
    foreign = _option_ids(db, other)

    _answer(client, task.id, [{"block_id": block.id, "option_ids": foreign}])

    assert get_state(db, block_id=block.id, user_id=user.id) is None


def test_ticks_come_back_on_reload(auth_client, db):
    """Закрытый блок открывается с проставленными галочками, а не пустым."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _rules_block(db, task, tail=False)
    options = _option_ids(db, block)
    _answer(client, task.id, [{"block_id": block.id, "option_ids": options}])

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    item = next(b for b in payload["blocks"] if b["id"] == block.id)

    assert item["answer_option_ids"] == sorted(options)
    assert [option["text"] for option in item["options"]] == list(RULES)
    assert item["answered"] is True


def test_rules_and_test_by_them_go_one_after_another(auth_client, db):
    """Сценарий первого дня: правила с галочками, следом тест по ним."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    blocks = sync_blocks(db, task_id=task.id, items=[
        {
            "block_type": BLOCK_RULES,
            "title": "Правила школы",
            "is_required": True,
            "options": [{"id": None, "text": text, "is_correct": False} for text in RULES],
        },
        {
            "block_type": BLOCK_QUESTION,
            "body": "Можно ли делать скриншоты материалов?",
            "question_type": "single",
            "is_required": True,
            "options": [
                {"text": "Нет", "is_correct": True},
                {"text": "Да", "is_correct": False},
            ],
        },
        {"block_type": BLOCK_TEXT, "body": "Дальше по программе"},
    ])
    db.commit()
    rules, question = blocks[0], blocks[1]

    assert _statuses(db, user) == ["current", "locked", "locked"]

    _answer(client, task.id, [
        {"block_id": rules.id, "option_ids": _option_ids(db, rules)}
    ])
    assert _statuses(db, user) == ["done", "current", "locked"]

    _answer(client, task.id, [
        {"block_id": question.id, "option_ids": _option_ids(db, question)[:1]}
    ])
    assert _statuses(db, user) == ["done", "done", "current"]


# ── конструктор преподавателя ───────────────────────────────────────────────

def test_constructor_offers_the_rules_block(admin_client):
    """Кнопка «Правила с галочками» и своя форма под неё, а не хвост вопроса."""
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert 'data-add-block="rules"' in page.text
    assert "type === 'rules'" in page.text
    assert "Правила с галочками" in page.text
