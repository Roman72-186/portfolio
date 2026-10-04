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
from app.services.tracker import create_task, task_done_for_user
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


def _task(db, owner, *, title="18 сентября", days=2):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY + timedelta(days=days))[0] + timedelta(hours=6),
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
        # Хвост — шаг, который можно отметить, а не текст: с 01.10.2026 текст
        # закрытого задания считается сделанным, и ответ на единственный вопрос
        # закрыл бы задание вместе с хвостом — «открылась ли очередь» не проверить.
        items.append({"block_type": "upload", "body": "Тест по правилам"})
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
        # Хвост — шаг, который можно отметить, а не текст: с 01.10.2026 текст
        # закрытого задания считается сделанным, и ответ на единственный вопрос
        # закрыл бы задание вместе с хвостом — «открылась ли очередь» не проверить.
        {"block_type": "upload", "body": "Дальше по программе"},
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


# ── галочки уходят сами (владелец 04.10.2026) ───────────────────────────────
#
# «После проставления галочки чек-бокса в „Правила с галочками“ считать
# задание выполненным и открывать следующее задание». Решение владельца в тот
# же день: правило автозакрытия не меняется — задание закрывается, когда
# сделаны все его шаги. Галочки закрывают задание, если правила — последний
# несделанный шаг; если за ними шаг того же задания (видео в задании 108
# «Старт годового курса»), открывается он.

def _blocks_payload(client, task_id, block_id):
    payload = client.get(f"/cabinet/tracker/tasks/{task_id}/blocks").json()
    return next(item for item in payload["blocks"] if item["id"] == block_id)


def test_rules_block_carries_its_own_submit_endpoint(auth_client, db):
    """Адрес у самого блока — по нему экран шлёт галочки без общей кнопки.

    После сохранения адреса нет: слать больше нечего, и общая форма тоже не
    рисуется (`lrnBlockRender.formOpen`).
    """
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _rules_block(db, task, tail=False)

    item = _blocks_payload(client, task.id, block.id)
    assert item["submit_endpoint"] == f"/cabinet/tracker/tasks/{task.id}/blocks"

    _answer(client, task.id, [{"block_id": block.id, "option_ids": _option_ids(db, block)}])

    item = _blocks_payload(client, task.id, block.id)
    assert item["submit_endpoint"] is None
    assert item["edit_reason"] == "Согласие с правилами уже сохранено."


def test_rules_only_task_closes_and_the_next_task_becomes_current(auth_client, db):
    """Задание из одних правил: последняя галочка закрывает задание, и следующее
    задание ленты открывается без кнопки «Завершить задание»."""
    client, user = auth_client
    _cycle(db, user)
    rules_task = _task(db, user, title="Старт годового курса")
    block = _rules_block(db, rules_task, tail=False)
    next_task = _task(db, user, title="1 неделя", days=3)
    sync_blocks(db, task_id=next_task.id, items=[
        {"block_type": "upload", "body": "Первая работа", "is_required": True},
    ])
    db.commit()

    assert _statuses(db, user) == ["current", "locked"]

    resp = _answer(client, rules_task.id, [
        {"block_id": block.id, "option_ids": _option_ids(db, block)}
    ])

    assert resp.status_code == 200
    assert task_done_for_user(db, rules_task.id, user.id)
    assert not task_done_for_user(db, next_task.id, user.id)
    assert _statuses(db, user) == ["done", "current"]


def test_rules_before_another_step_open_it_and_keep_the_task_open(auth_client, db):
    """Выбор владельца 04.10.2026: за правилами шаг того же задания — галочки
    открывают его, а задание остаётся открытым, пока шаг не сделан. Следующее
    задание при этом заперто."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    # Как в задании 108: правила, за ними обязательный шаг того же задания.
    block = sync_blocks(db, task_id=task.id, items=[
        {
            "block_type": BLOCK_RULES, "title": "Правила на годовом курсе", "is_required": True,
            "options": [{"id": None, "text": text, "is_correct": False} for text in RULES],
        },
        {"block_type": "upload", "body": "Устройство месяца", "is_required": True},
    ])[0]
    next_task = _task(db, user, title="1 неделя", days=3)
    sync_blocks(db, task_id=next_task.id, items=[
        {"block_type": "upload", "body": "Первая работа", "is_required": True},
    ])
    db.commit()

    assert _statuses(db, user) == ["current", "locked", "locked"]

    _answer(client, task.id, [{"block_id": block.id, "option_ids": _option_ids(db, block)}])

    assert not task_done_for_user(db, task.id, user.id)
    assert _statuses(db, user) == ["done", "current", "locked"]


def test_screens_send_ticks_by_themselves():
    """Галочки шлёт общий рендерер, а не кнопка формы, — и обоим экранам одной функцией.

    Статическая проверка исходников: запрос идёт через `post()` со свежим
    ключом (`tests/test_csrf_freshness.py`), правила не уходят в общую форму,
    условие «нужна ли форма» не копируется в экраны, а лента после
    сохранения переходит к открывшемуся шагу.
    """
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    render = (app_dir / "static" / "js" / "task-blocks-render.js").read_text(encoding="utf-8")
    feed = (app_dir / "static" / "js" / "cycle-feed.js").read_text(encoding="utf-8")
    tracker = (app_dir / "templates" / "partials" / "inline" / "task_blocks.html").read_text(encoding="utf-8")
    learning = (app_dir / "templates" / "cabinet_learning.html").read_text(encoding="utf-8")

    assert "function wireRulesAutosave(" in render
    assert "post(block.submit_endpoint, {" in render.split("function wireRulesAutosave(")[1]
    # Правила со своим адресом в общую форму не идут.
    assert "if (block.submit_endpoint) return;" in render
    for screen in (feed, tracker):
        assert "window.lrnBlockRender.formOpen(" in screen
        assert "['question', 'scale', 'rules']" not in screen
    assert "onRulesSaved: showStepAfter" in feed
    assert "searchParams.set('after'" in feed
    assert "lrnFocusAfterStep(after)" in learning


# ── форматирование текста ───────────────────────────────────────────────────
# Владелец 04.10.2026: «слетает форматирование текста… настройка текста в
# полях и блоках должна переиспользоваться от эталонной». Текст согласия шёл
# полужирным шрифтом вопроса (выделенное жирным терялось), у текста пункта
# была своя копия стилей, а подпись у галочки — сырая строка со звёздочками.

def test_rules_label_comes_formatted_like_a_question_option(auth_client, db):
    """Подпись у галочки — готовый HTML из `format_rich_text`, как у варианта вопроса."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    block = _rules_block(db, task, rules=("**Ознакомлен(а)** с правилами",), tail=False)

    item = _blocks_payload(client, task.id, block.id)

    assert item["options"][0]["text_html"] == "<strong>Ознакомлен(а)</strong> с правилами"


def test_rules_texts_use_the_reference_text_body():
    """Текст согласия и текст пункта рисуются эталонным телом текстового блока.

    Статическая проверка: `renderRules` берёт `.lrn-blk-body`, как `renderText`,
    своей копии стилей у правил нет, а жирное слово в подписи не рвёт строку —
    отдельной строкой его ставит только развёрнутый вариант диагностики.
    """
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    render = (app_dir / "static" / "js" / "task-blocks-render.js").read_text(encoding="utf-8")
    css = (app_dir / "static" / "css" / "tracker.css").read_text(encoding="utf-8")

    rules = render.split("function renderRules(")[1].split("function wireRulesAutosave(")[0]
    assert "elHtml('p', 'lrn-blk-body', block.body_html)" in rules
    assert "lrn-blk-question-body" not in rules
    assert "elHtml('span', null, option.text_html)" in rules
    item = render.split("function ruleItemContent(")[1].split("function renderCompare(")[0]
    assert "elHtml('div', 'lrn-blk-body', option.description_html)" in item
    assert "'lrn-blk-rule-text'" not in render
    assert ".lrn-blk-rule-text {" not in css
    assert ".lrn-blk-body ul {" in css
    assert ".lrn-blk-option > span > strong" not in css


# ── конструктор преподавателя ───────────────────────────────────────────────

def test_constructor_offers_the_rules_block(admin_client):
    """Кнопка «Правила с галочками» и своя форма под неё, а не хвост вопроса."""
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert 'data-add-block="rules"' in page.text
    assert "type === 'rules'" in page.text
    assert "Правила с галочками" in page.text
