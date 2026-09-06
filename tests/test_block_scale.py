"""Диагностика навыков — блок «Шкала навыков» (владелец 03.09.2026).

«Есть навыки, уровень сформированности навыка… оцени, насколько ты
стрессоустойчивый, оцени свою уверенность… и ребёнок отмечает 3 из 10 или
5 из 10… нужно сделать, чтобы эта информация погружалась ему в его личную
вкладку… в начале обучения было так, в середине уже вот так.»

Отдельной таблицы под диагностику нет: навыки — варианты блока, оценка —
текст выбранного варианта.
"""
from datetime import timedelta

from app.models.task_block import (
    BLOCK_SCALE,
    TaskBlock,
    TaskBlockOption,
)
from app.models.tracker import TrackerTask
from app.services.skills_history import skills_history
from app.services.task_blocks import (
    get_selected_option_texts,
    grade_response,
    question_blocks,
    save_response,
    sync_blocks,
)
from app.services.tz import today_msk

TODAY = today_msk()


def _task(db, title="Диагностика"):
    task = TrackerTask(title=title, kind="material")
    db.add(task)
    db.flush()
    return task


def _scale(db, task, skills=("Стрессоустойчивость", "Уверенность")):
    blocks = sync_blocks(db, task_id=task.id, items=[{
        "block_type": BLOCK_SCALE,
        "title": "Оцени себя",
        "body": "От 1 до 10",
        "options": [{"id": None, "text": skill, "is_correct": False} for skill in skills],
    }])
    db.commit()
    return blocks[0]


# ── конструктор ─────────────────────────────────────────────────────────────

def test_scale_keeps_its_skills(db):
    task = _task(db)
    block = _scale(db, task)

    options = db.query(TaskBlockOption).filter(TaskBlockOption.block_id == block.id).all()
    assert sorted(o.text for o in options) == ["Стрессоустойчивость", "Уверенность"]


def test_scale_without_skills_is_dropped(db):
    """Шкала без навыков бессмысленна — как вопрос без текста."""
    task = _task(db)
    blocks = sync_blocks(db, task_id=task.id, items=[{
        "block_type": BLOCK_SCALE, "title": "Пустая", "options": [],
    }])
    db.commit()

    assert blocks == []


def test_scale_is_answerable_but_not_graded(db):
    """Самооценку не с чем сравнивать: верного ответа у неё нет."""
    task = _task(db)
    block = _scale(db, task)

    assert block in question_blocks([block])
    verdict = grade_response(db, blocks=[block], response_id=None)
    assert verdict["gradable_count"] == 0


# ── ответ ученика ───────────────────────────────────────────────────────────

def test_student_scores_every_skill(db, regular_user):
    task = _task(db)
    block = _scale(db, task)
    options = db.query(TaskBlockOption).filter(TaskBlockOption.block_id == block.id).all()
    by_text = {o.text: o.id for o in options}

    response = save_response(
        db, task_id=task.id, user_id=regular_user.id, blocks=[block],
        answers={block.id: {
            "option_ids": [by_text["Стрессоустойчивость"], by_text["Уверенность"]],
            "option_texts": {
                by_text["Стрессоустойчивость"]: "3",
                by_text["Уверенность"]: "7",
            },
        }},
    )
    db.commit()

    saved = get_selected_option_texts(db, response_id=response.id)
    assert saved[by_text["Стрессоустойчивость"]] == "3"
    assert saved[by_text["Уверенность"]] == "7"


def test_scale_scores_do_not_need_requires_text_flag(db, regular_user):
    """У обычного вопроса текст варианта принимается только с флагом
    `requires_text`; у шкалы текст — это и есть оценка."""
    task = _task(db)
    block = _scale(db, task, skills=("Самостоятельность",))
    option = db.query(TaskBlockOption).filter(TaskBlockOption.block_id == block.id).one()
    assert option.requires_text is False

    response = save_response(
        db, task_id=task.id, user_id=regular_user.id, blocks=[block],
        answers={block.id: {
            "option_ids": [option.id],
            "option_texts": {option.id: "5"},
        }},
    )
    db.commit()

    assert get_selected_option_texts(db, response_id=response.id)[option.id] == "5"


# ── история в профиле ───────────────────────────────────────────────────────

def test_history_is_empty_without_diagnostics(db, regular_user):
    assert skills_history(db, regular_user.id) == []


def test_history_collects_scores_by_skill(db, regular_user):
    task = _task(db)
    block = _scale(db, task, skills=("Уверенность",))
    option = db.query(TaskBlockOption).filter(TaskBlockOption.block_id == block.id).one()
    save_response(
        db, task_id=task.id, user_id=regular_user.id, blocks=[block],
        answers={block.id: {"option_ids": [option.id], "option_texts": {option.id: "4"}}},
    )
    db.commit()

    history = skills_history(db, regular_user.id)

    assert len(history) == 1
    assert history[0]["skill"] == "Уверенность"
    assert [p["score"] for p in history[0]["points"]] == [4]


def test_history_puts_the_same_skill_from_two_diagnostics_together(db, regular_user):
    """«Наложить друг на друга и посмотреть рост» — навык опознаётся по
    названию, а не по номеру варианта: диагностики разные, навык один."""
    first_task = _task(db, "Диагностика в начале")
    first_block = _scale(db, first_task, skills=("Уверенность",))
    first_option = db.query(TaskBlockOption).filter(
        TaskBlockOption.block_id == first_block.id
    ).one()
    save_response(
        db, task_id=first_task.id, user_id=regular_user.id, blocks=[first_block],
        answers={first_block.id: {
            "option_ids": [first_option.id], "option_texts": {first_option.id: "3"},
        }},
    )
    second_task = _task(db, "Диагностика в середине")
    second_block = _scale(db, second_task, skills=("Уверенность",))
    second_option = db.query(TaskBlockOption).filter(
        TaskBlockOption.block_id == second_block.id
    ).one()
    save_response(
        db, task_id=second_task.id, user_id=regular_user.id, blocks=[second_block],
        answers={second_block.id: {
            "option_ids": [second_option.id], "option_texts": {second_option.id: "8"},
        }},
    )
    db.commit()

    history = skills_history(db, regular_user.id)

    assert len(history) == 1
    assert sorted(p["score"] for p in history[0]["points"]) == [3, 8]


def test_history_skips_broken_scores(db, regular_user):
    """Оценка не числом — из старого ответа или ручной правки: пропускаем
    точку, а не роняем весь раздел."""
    task = _task(db)
    block = _scale(db, task, skills=("Уверенность",))
    option = db.query(TaskBlockOption).filter(TaskBlockOption.block_id == block.id).one()
    save_response(
        db, task_id=task.id, user_id=regular_user.id, blocks=[block],
        answers={block.id: {"option_ids": [option.id], "option_texts": {option.id: "почти"}}},
    )
    db.commit()

    assert skills_history(db, regular_user.id) == []


# ── экраны ──────────────────────────────────────────────────────────────────

def test_personal_page_shows_skills(auth_client, db):
    client, user = auth_client
    task = _task(db)
    block = _scale(db, task, skills=("Стрессоустойчивость",))
    option = db.query(TaskBlockOption).filter(TaskBlockOption.block_id == block.id).one()
    save_response(
        db, task_id=task.id, user_id=user.id, blocks=[block],
        answers={block.id: {"option_ids": [option.id], "option_texts": {option.id: "6"}}},
    )
    db.commit()

    page = client.get("/cabinet/personal")

    assert "Мои навыки" in page.text
    assert "Стрессоустойчивость" in page.text


def test_personal_page_without_skills_has_no_section(auth_client):
    client, _ = auth_client

    page = client.get("/cabinet/personal")

    assert "Мои навыки" not in page.text


def test_constructor_offers_the_scale_block(admin_client):
    client, _ = admin_client
    day = (TODAY + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}")

    assert 'data-add-block="scale"' in page.text
    assert "Шкала навыков" in page.text
