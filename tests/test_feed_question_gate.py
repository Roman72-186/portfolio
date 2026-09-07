"""Обязательный вопрос в ленте: ответил — идёшь дальше (найдено 07.09.2026).

Замысел созвона 03.09.2026 — «сделал действие, получил доступ дальше, не сделал
действие, не получил». На деле лента запиралась в обе стороны:

1. **Ответ не закрывал блок.** Ученик отвечал, ответ сохранялся, состояния
   блока не появлялось — и обязательный вопрос держал закрытым весь хвост
   навсегда. Первый день предобучения (тест по правилам, опрос после видео)
   этим и собирается.
2. **Скрытый вопрос вёл себя как обычный.** «Показать только после того, как
   ученик закроет задание» лента игнорировала: вопрос стоял в ней шагом и,
   отмеченный обязательным, запирал хвост — ответить нельзя, потому что не
   видно, а не ответишь, дальше не пустят.
"""
from datetime import timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import BLOCK_QUESTION, BLOCK_SCALE, BLOCK_TEXT, TaskBlock
from app.services.cycle_feed import build_cycle_feed
from app.services.program import day_bounds
from app.services.task_blocks import get_options, get_state, sync_blocks
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=1)
CYCLE_END = TODAY + timedelta(days=6)


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


def _task(db, owner, *, title="День"):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _statuses(db, user):
    steps = build_cycle_feed(
        db, user_id=user.id, user_tariff=user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )
    return [step["status"] for step in steps]


def _answer(client, task_id, answers):
    return client.post(f"/cabinet/tracker/tasks/{task_id}/blocks", json={"answers": answers})


def test_answering_a_required_question_opens_the_tail(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    question = TaskBlock(
        task_id=task.id, block_type=BLOCK_QUESTION, body="Согласны с правилами?",
        question_type="text", sort_order=1, is_required=True,
    )
    db.add_all([
        question,
        TaskBlock(task_id=task.id, block_type=BLOCK_TEXT, body="Дальше", sort_order=2),
    ])
    db.commit()

    assert _statuses(db, user) == ["current", "locked"]

    resp = _answer(client, task.id, [{"block_id": question.id, "text": "Согласен"}])

    assert resp.status_code == 200
    assert _statuses(db, user) == ["done", "current"]


def test_answer_with_options_closes_the_block(auth_client, db):
    """Тест по правилам: варианты вместо свободного текста."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    blocks = sync_blocks(db, task_id=task.id, items=[
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
        {"block_type": BLOCK_TEXT, "body": "Дальше"},
    ])
    db.commit()
    question = blocks[0]
    option_id = get_options(db, [question.id])[question.id][0].id

    _answer(client, task.id, [{"block_id": question.id, "option_ids": [option_id]}])

    assert _statuses(db, user) == ["done", "current"]


def _two_required_questions(db, task):
    first = TaskBlock(
        task_id=task.id, block_type=BLOCK_QUESTION, body="Первый",
        question_type="text", sort_order=1, is_required=True,
    )
    second = TaskBlock(
        task_id=task.id, block_type=BLOCK_QUESTION, body="Второй",
        question_type="text", sort_order=2, is_required=True,
    )
    db.add_all([
        first, second,
        TaskBlock(task_id=task.id, block_type=BLOCK_TEXT, body="Хвост", sort_order=3),
    ])
    db.commit()
    return first, second


def test_unanswered_required_question_still_holds_the_tail(auth_client, db):
    """Закрываются только те блоки, на которые пришёл ответ."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    first, _ = _two_required_questions(db, task)

    _answer(client, task.id, [{"block_id": first.id, "text": "Ответ"}])

    assert _statuses(db, user) == ["done", "current", "locked"]


def test_skipped_question_can_be_answered_later(auth_client, db):
    """Пропущенный вопрос остаётся достижимым.

    «Одна попытка» (владелец 31.08.2026) считается по вопросу, а не по
    заданию: иначе ученик, оставивший поле пустым, запирал себе ленту
    навсегда — форма отправки исчезала вместе с первой отправкой.
    """
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    first, second = _two_required_questions(db, task)

    _answer(client, task.id, [{"block_id": first.id, "text": "Ответ"}])
    panel = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    assert panel["submit_endpoint"] is not None
    assert panel["answered"] is False

    resp = _answer(client, task.id, [{"block_id": second.id, "text": "Второй"}])

    assert resp.status_code == 200
    assert _statuses(db, user) == ["done", "done", "current"]


def test_answered_question_cannot_be_rewritten(auth_client, db):
    """Правило «одна попытка» остаётся: ответ переотправить нельзя."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    first, _ = _two_required_questions(db, task)

    _answer(client, task.id, [{"block_id": first.id, "text": "Ответ"}])
    again = _answer(client, task.id, [{"block_id": first.id, "text": "Передумал"}])

    assert again.status_code == 409
    panel = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    answered_first = [b for b in panel["blocks"] if b["id"] == first.id][0]
    assert answered_first["answered"] is True
    assert answered_first["answer_text"] == "Ответ"


def test_form_closes_when_every_question_is_answered(auth_client, db):
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    first, second = _two_required_questions(db, task)

    _answer(client, task.id, [
        {"block_id": first.id, "text": "Раз"},
        {"block_id": second.id, "text": "Два"},
    ])

    panel = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    assert panel["answered"] is True
    assert panel["submit_endpoint"] is None


def test_foreign_option_id_does_not_close_the_block(auth_client, db):
    """Вариант из чужого блока отбрасывается — и ничего не закрывает."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    blocks = sync_blocks(db, task_id=task.id, items=[
        {
            "block_type": BLOCK_QUESTION,
            "body": "Выберите вариант",
            "question_type": "single",
            "is_required": True,
            "options": [{"text": "Да", "is_correct": True}],
        },
        {"block_type": BLOCK_TEXT, "body": "Хвост"},
    ])
    db.commit()
    question = blocks[0]

    _answer(client, task.id, [{"block_id": question.id, "option_ids": [10_000]}])

    assert get_state(db, block_id=question.id, user_id=user.id) is None
    assert _statuses(db, user) == ["current", "locked"]


def test_scale_answer_closes_the_block(auth_client, db):
    """Шкала навыков сохраняется тем же путём, значит и закрывается так же."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    blocks = sync_blocks(db, task_id=task.id, items=[
        {
            "block_type": BLOCK_SCALE,
            "title": "Оцени себя",
            "is_required": True,
            "options": [{"text": "Стрессоустойчивость"}],
        },
        {"block_type": BLOCK_TEXT, "body": "Дальше"},
    ])
    db.commit()
    scale = blocks[0]
    option_id = get_options(db, [scale.id])[scale.id][0].id

    _answer(client, task.id, [{
        "block_id": scale.id,
        "option_ids": [option_id],
        "option_texts": {option_id: "7"},
    }])

    assert _statuses(db, user) == ["done", "current"]


def test_hidden_question_is_out_of_the_feed_until_the_task_is_done(auth_client, db):
    """Скрытый вопрос не шаг ленты и никого не блокирует.

    Иначе выходил бы тупик без выхода: блок обязателен, но невидим.
    """
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    db.add_all([
        TaskBlock(
            task_id=task.id, block_type=BLOCK_QUESTION, body="Как прошло?",
            question_type="text", sort_order=1, is_required=True,
            hidden_until_done=True,
        ),
        TaskBlock(task_id=task.id, block_type=BLOCK_TEXT, body="Материал", sort_order=2),
    ])
    db.commit()

    assert _statuses(db, user) == ["current"]


def test_state_carries_the_source_of_closing(auth_client, db):
    """Видно, чем закрыт блок: ответом, загрузкой работы или галочкой."""
    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    question = TaskBlock(
        task_id=task.id, block_type=BLOCK_QUESTION, body="Вопрос",
        question_type="text", sort_order=1, is_required=True,
    )
    db.add(question)
    db.commit()

    _answer(client, task.id, [{"block_id": question.id, "text": "Ответ"}])

    assert get_state(db, block_id=question.id, user_id=user.id).completion_source == "answer"


def test_hidden_question_is_answerable_after_the_task_is_done(auth_client, db):
    """Вернувшись в ленту после закрытия задания, скрытый вопрос отвечаем.

    Иначе фикс выше просто отодвигал бы тупик: блок обязателен, виден, а форма
    отправки уже закрыта первой попыткой.
    """
    from app.services.tracker import close_task_for_user

    client, user = auth_client
    _cycle(db, user)
    task = _task(db, user)
    visible = TaskBlock(
        task_id=task.id, block_type=BLOCK_QUESTION, body="Открытый",
        question_type="text", sort_order=1, is_required=True,
    )
    hidden = TaskBlock(
        task_id=task.id, block_type=BLOCK_QUESTION, body="Как прошло?",
        question_type="text", sort_order=2, is_required=True,
        hidden_until_done=True,
    )
    db.add_all([visible, hidden])
    db.commit()

    _answer(client, task.id, [{"block_id": visible.id, "text": "Ответ"}])
    close_task_for_user(db, task, user.id, source="auto")
    db.commit()

    panel = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()
    assert panel["submit_endpoint"] is not None

    resp = _answer(client, task.id, [{"block_id": hidden.id, "text": "Нормально"}])

    assert resp.status_code == 200
    assert get_state(db, block_id=hidden.id, user_id=user.id).status == "done"
