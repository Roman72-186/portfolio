"""Универсальный конструктор содержимого элемента дня — сервисный слой.

Держит два контракта, ради которых модуль и написан: правка списка блоков не
рвёт уже сохранённые ответы учеников, а удаление блока уносит их вместе с
собой (SQLite в тестах не исполняет ON DELETE CASCADE, чистка явная).
"""

from datetime import date, datetime, timezone

from app.models.learning_video import LearningVideo
from app.models.task_block import (
    BLOCK_LINK,
    BLOCK_PHOTO,
    BLOCK_QUESTION,
    BLOCK_TEXT,
    BLOCK_VIDEO,
    QUESTION_MULTIPLE,
    QUESTION_SINGLE,
    QUESTION_TEXT,
    TaskBlock,
    TaskBlockAnswer,
    TaskBlockAnswerOption,
    TaskBlockOption,
    TaskBlockState,
)
from app.models.tracker import STATUS_DONE, STATUS_OPEN, TrackerTask
from app.services.task_blocks import (
    block_status,
    close_block_for_user,
    feed_state,
    get_answers_map,
    get_blocks,
    get_blocks_for_tasks,
    get_images,
    get_options,
    get_response,
    get_selected_options,
    get_state,
    get_states,
    get_tariffs,
    is_block_accessible,
    question_blocks,
    save_response,
    sync_blocks,
)


def _task(db, title="Материал недели") -> TrackerTask:
    task = TrackerTask(title=title, kind="material")
    db.add(task)
    db.flush()
    return task


def _video(db) -> LearningVideo:
    video = LearningVideo(bunny_library_id=1, bunny_video_id="v-block", title="Урок")
    db.add(video)
    db.flush()
    return video


def _text(body, **extra):
    return {"block_type": BLOCK_TEXT, "body": body, **extra}


def _question(body, question_type=QUESTION_TEXT, options=None, **extra):
    return {
        "block_type": BLOCK_QUESTION,
        "body": body,
        "question_type": question_type,
        "options": options or [],
        **extra,
    }


# --- состав и порядок -------------------------------------------------------


def test_sync_blocks_keeps_given_order(db):
    task = _task(db)
    rows = sync_blocks(
        db,
        task_id=task.id,
        items=[_text("Первый"), _text("Второй"), _text("Третий")],
    )
    db.commit()

    assert [row.body for row in rows] == ["Первый", "Второй", "Третий"]
    assert [row.sort_order for row in rows] == [0, 1, 2]


def test_sync_blocks_accepts_every_type_in_one_task(db):
    """Смысл всей стройки: в один элемент кладётся что угодно вперемешку."""
    task = _task(db)
    video = _video(db)
    sync_blocks(
        db,
        task_id=task.id,
        items=[
            _text("Прочитай перед началом"),
            {"block_type": BLOCK_PHOTO, "images": [{"url": "https://s3/a.jpg", "path": "p/a.jpg"}]},
            {"block_type": BLOCK_VIDEO, "video_id": video.id, "title": "Разбор"},
            {"block_type": BLOCK_LINK, "url": "https://example.org", "title": "Читать"},
            _question("Что было главным?"),
        ],
    )
    db.commit()

    blocks = get_blocks(db, task.id)
    assert [b.block_type for b in blocks] == [
        BLOCK_TEXT, BLOCK_PHOTO, BLOCK_VIDEO, BLOCK_LINK, BLOCK_QUESTION
    ]
    assert [i.image_s3_url for i in get_images(db, [blocks[1].id])[blocks[1].id]] == ["https://s3/a.jpg"]
    assert blocks[2].video_id == video.id
    assert blocks[3].url == "https://example.org"


def test_sync_blocks_skips_empty_drafts(db):
    """Нажали «плюс» и не заполнили — блок молча отбрасывается, как это делали
    пустые вопросы мини-опроса."""
    task = _task(db)
    video = _video(db)
    rows = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _text("   "),
            {"block_type": BLOCK_PHOTO, "images": []},
            {"block_type": BLOCK_VIDEO, "video_id": None},
            {"block_type": BLOCK_LINK, "url": "  "},
            _text("Единственный настоящий"),
            {"block_type": BLOCK_VIDEO, "video_id": video.id},
        ],
    )
    db.commit()

    assert [row.body for row in rows] == ["Единственный настоящий", None]
    assert [row.sort_order for row in rows] == [0, 1]


def test_sync_blocks_ignores_unknown_type(db):
    task = _task(db)
    rows = sync_blocks(
        db, task_id=task.id, items=[{"block_type": "audio", "body": "Подкаст"}]
    )
    db.commit()

    assert rows == []
    assert get_blocks(db, task.id) == []


def test_get_blocks_for_tasks_groups_by_task(db):
    first, second = _task(db, "Первый"), _task(db, "Второй")
    sync_blocks(db, task_id=first.id, items=[_text("A"), _text("B")])
    sync_blocks(db, task_id=second.id, items=[_text("C")])
    db.commit()

    grouped = get_blocks_for_tasks(db, [first.id, second.id])
    assert [b.body for b in grouped[first.id]] == ["A", "B"]
    assert [b.body for b in grouped[second.id]] == ["C"]
    assert get_blocks_for_tasks(db, []) == {}


# --- правка не рвёт ответы --------------------------------------------------


def test_editing_block_by_id_keeps_student_answers(db, user_factory):
    """Правка текста существующего блока (тот же id) не задевает ответы."""
    task = _task(db)
    student = user_factory(vk_id=700_101, name="Ученик")
    [block] = sync_blocks(db, task_id=task.id, items=[_question("Черновой текст")])
    db.commit()

    save_response(
        db,
        task_id=task.id,
        user_id=student.id,
        blocks=[block],
        answers={block.id: {"text": "Мой ответ"}},
    )
    db.commit()

    sync_blocks(
        db,
        task_id=task.id,
        items=[_question("Уточнённый текст", id=block.id)],
    )
    db.commit()

    response = get_response(db, task_id=task.id, user_id=student.id)
    assert get_answers_map(db, response_id=response.id) == {block.id: "Мой ответ"}
    assert db.get(TaskBlock, block.id).body == "Уточнённый текст"


def test_new_block_alongside_existing_does_not_disturb_answers(db, user_factory):
    task = _task(db)
    student = user_factory(vk_id=700_102, name="Ученик")
    [block] = sync_blocks(db, task_id=task.id, items=[_question("Первый вопрос")])
    db.commit()
    save_response(
        db,
        task_id=task.id,
        user_id=student.id,
        blocks=[block],
        answers={block.id: {"text": "Ответ"}},
    )
    db.commit()

    rows = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _text("Вставили текст сверху"),
            _question("Первый вопрос", id=block.id),
        ],
    )
    db.commit()

    assert rows[1].id == block.id
    assert rows[1].sort_order == 1
    response = get_response(db, task_id=task.id, user_id=student.id)
    assert get_answers_map(db, response_id=response.id) == {block.id: "Ответ"}


def test_removing_block_deletes_its_answers(db, user_factory):
    task = _task(db)
    student = user_factory(vk_id=700_103, name="Ученик")
    kept, dropped = sync_blocks(
        db, task_id=task.id, items=[_question("Останется"), _question("Уйдёт")]
    )
    db.commit()
    kept_id, dropped_id = kept.id, dropped.id
    save_response(
        db,
        task_id=task.id,
        user_id=student.id,
        blocks=[kept, dropped],
        answers={kept_id: {"text": "Раз"}, dropped_id: {"text": "Два"}},
    )
    db.commit()

    sync_blocks(db, task_id=task.id, items=[_question("Останется", id=kept_id)])
    db.commit()

    assert db.get(TaskBlock, dropped_id) is None
    assert (
        db.query(TaskBlockAnswer).filter(TaskBlockAnswer.block_id == dropped_id).count()
        == 0
    )
    response = get_response(db, task_id=task.id, user_id=student.id)
    assert get_answers_map(db, response_id=response.id) == {kept_id: "Раз"}


# --- варианты ответа --------------------------------------------------------


def test_options_saved_only_for_choice_questions(db):
    task = _task(db)
    free, choice = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question("Свободный", options=[{"text": "Лишний вариант"}]),
            _question(
                "С вариантами",
                question_type=QUESTION_SINGLE,
                options=[
                    {"text": "Верный", "is_correct": True},
                    {"text": "Неверный"},
                ],
            ),
        ],
    )
    db.commit()

    options = get_options(db, [free.id, choice.id])
    assert free.id not in options
    assert [(o.text, o.is_correct) for o in options[choice.id]] == [
        ("Верный", True),
        ("Неверный", False),
    ]


def test_switching_question_to_free_text_drops_options(db):
    task = _task(db)
    [block] = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question(
                "Вопрос",
                question_type=QUESTION_MULTIPLE,
                options=[{"text": "A"}, {"text": "B"}],
            )
        ],
    )
    db.commit()
    assert len(get_options(db, [block.id])[block.id]) == 2

    sync_blocks(
        db,
        task_id=task.id,
        items=[_question("Вопрос", question_type=QUESTION_TEXT, id=block.id)],
    )
    db.commit()

    assert db.query(TaskBlockOption).filter(TaskBlockOption.block_id == block.id).count() == 0


def test_editing_option_by_id_keeps_student_choice(db, user_factory):
    task = _task(db)
    student = user_factory(vk_id=700_104, name="Ученик")
    [block] = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question(
                "Вопрос",
                question_type=QUESTION_SINGLE,
                options=[{"text": "Первый"}, {"text": "Второй"}],
            )
        ],
    )
    db.commit()
    first, second = get_options(db, [block.id])[block.id]

    save_response(
        db,
        task_id=task.id,
        user_id=student.id,
        blocks=[block],
        answers={block.id: {"option_ids": [second.id]}},
    )
    db.commit()

    sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question(
                "Вопрос",
                question_type=QUESTION_SINGLE,
                id=block.id,
                options=[
                    {"id": first.id, "text": "Первый, поправленный"},
                    {"id": second.id, "text": "Второй"},
                ],
            )
        ],
    )
    db.commit()

    response = get_response(db, task_id=task.id, user_id=student.id)
    assert get_selected_options(db, response_id=response.id) == {block.id: {second.id}}


def test_removing_option_removes_it_from_saved_answers(db, user_factory):
    task = _task(db)
    student = user_factory(vk_id=700_105, name="Ученик")
    [block] = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question(
                "Вопрос",
                question_type=QUESTION_MULTIPLE,
                options=[{"text": "Остаётся"}, {"text": "Уходит"}],
            )
        ],
    )
    db.commit()
    kept, dropped = get_options(db, [block.id])[block.id]
    kept_id, dropped_id = kept.id, dropped.id

    save_response(
        db,
        task_id=task.id,
        user_id=student.id,
        blocks=[block],
        answers={block.id: {"option_ids": [kept_id, dropped_id]}},
    )
    db.commit()

    sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question(
                "Вопрос",
                question_type=QUESTION_MULTIPLE,
                id=block.id,
                options=[{"id": kept_id, "text": "Остаётся"}],
            )
        ],
    )
    db.commit()

    response = get_response(db, task_id=task.id, user_id=student.id)
    assert get_selected_options(db, response_id=response.id) == {block.id: {kept_id}}
    assert (
        db.query(TaskBlockAnswerOption)
        .filter(TaskBlockAnswerOption.option_id == dropped_id)
        .count()
        == 0
    )


# --- ответы ученика ---------------------------------------------------------


def test_save_response_is_idempotent(db, user_factory):
    """Повторная отправка обновляет то же заполнение, а не заводит второе."""
    task = _task(db)
    student = user_factory(vk_id=700_106, name="Ученик")
    [block] = sync_blocks(db, task_id=task.id, items=[_question("Вопрос")])
    db.commit()

    save_response(
        db, task_id=task.id, user_id=student.id, blocks=[block],
        answers={block.id: {"text": "Первая версия"}},
    )
    db.commit()
    save_response(
        db, task_id=task.id, user_id=student.id, blocks=[block],
        answers={block.id: {"text": "Вторая версия"}},
    )
    db.commit()

    response = get_response(db, task_id=task.id, user_id=student.id)
    assert get_answers_map(db, response_id=response.id) == {block.id: "Вторая версия"}
    assert db.query(TaskBlockAnswer).filter(
        TaskBlockAnswer.response_id == response.id
    ).count() == 1


def test_save_response_rejects_option_from_another_block(db, user_factory):
    """Чужой option_id в теле запроса не должен попасть в ответ."""
    task = _task(db)
    student = user_factory(vk_id=700_107, name="Ученик")
    mine, other = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question("Мой", question_type=QUESTION_SINGLE, options=[{"text": "Свой"}]),
            _question("Чужой", question_type=QUESTION_SINGLE, options=[{"text": "Чужой"}]),
        ],
    )
    db.commit()
    options = get_options(db, [mine.id, other.id])
    foreign_id = options[other.id][0].id

    save_response(
        db,
        task_id=task.id,
        user_id=student.id,
        blocks=[mine],
        answers={mine.id: {"option_ids": [foreign_id]}},
    )
    db.commit()

    response = get_response(db, task_id=task.id, user_id=student.id)
    assert get_selected_options(db, response_id=response.id) == {}


def test_question_blocks_filters_content(db):
    task = _task(db)
    sync_blocks(
        db,
        task_id=task.id,
        items=[_text("Просто текст"), _question("А это вопрос")],
    )
    db.commit()

    assert [b.body for b in question_blocks(get_blocks(db, task.id))] == ["А это вопрос"]


# --- копирование недели ------------------------------------------------------


def test_copy_task_blocks_clones_content_and_options(db):
    """«Скопировать неделю» переносит содержимое, включая ролик: привязка
    больше не в единственной колонке LearningVideo.topic_id."""
    from app.services.tracker import copy_task_blocks

    source, target = _task(db, "Исходный"), _task(db, "Копия")
    video = _video(db)
    sync_blocks(
        db,
        task_id=source.id,
        items=[
            _text("Вступление"),
            {"block_type": BLOCK_VIDEO, "video_id": video.id},
            _question(
                "Выбери верное",
                question_type=QUESTION_SINGLE,
                options=[{"text": "Да", "is_correct": True}, {"text": "Нет"}],
            ),
        ],
    )
    db.commit()

    copy_task_blocks(db, from_task_id=source.id, to_task_id=target.id)
    db.commit()

    copied = get_blocks(db, target.id)
    assert [b.block_type for b in copied] == [BLOCK_TEXT, BLOCK_VIDEO, BLOCK_QUESTION]
    assert copied[1].video_id == video.id
    options = get_options(db, [copied[2].id])[copied[2].id]
    assert [(o.text, o.is_correct) for o in options] == [("Да", True), ("Нет", False)]
    # Исходные блоки на месте — копия их не забрала.
    assert len(get_blocks(db, source.id)) == 3


def test_removing_chosen_option_leaves_no_phantom_answer(db, user_factory):
    """Удалили вариант, который ученик выбрал, — строка ответа не должна
    остаться пустой: визуально это чистая форма, но по данным блок выглядел бы
    отвеченным."""
    task = _task(db)
    student = user_factory(vk_id=700_108, name="Ученик")
    [block] = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question(
                "Вопрос",
                question_type=QUESTION_SINGLE,
                options=[{"text": "Выбранный"}, {"text": "Другой"}],
            )
        ],
    )
    db.commit()
    chosen, other = get_options(db, [block.id])[block.id]
    save_response(
        db, task_id=task.id, user_id=student.id, blocks=[block],
        answers={block.id: {"option_ids": [chosen.id]}},
    )
    db.commit()

    sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question(
                "Вопрос", question_type=QUESTION_SINGLE, id=block.id,
                options=[{"id": other.id, "text": "Другой"}],
            )
        ],
    )
    db.commit()

    response = get_response(db, task_id=task.id, user_id=student.id)
    assert get_answers_map(db, response_id=response.id) == {}
    assert db.query(TaskBlockAnswer).filter(
        TaskBlockAnswer.response_id == response.id
    ).count() == 0


def test_pruning_keeps_answers_that_still_have_content(db, user_factory):
    """Чистка пустых не должна задевать ответ с текстом или уцелевшим выбором."""
    task = _task(db)
    student = user_factory(vk_id=700_109, name="Ученик")
    [block] = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question(
                "Вопрос",
                question_type=QUESTION_MULTIPLE,
                options=[{"text": "Останется"}, {"text": "Уйдёт"}],
            )
        ],
    )
    db.commit()
    kept, dropped = get_options(db, [block.id])[block.id]
    save_response(
        db, task_id=task.id, user_id=student.id, blocks=[block],
        answers={block.id: {"option_ids": [kept.id, dropped.id]}},
    )
    db.commit()

    sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question(
                "Вопрос", question_type=QUESTION_MULTIPLE, id=block.id,
                options=[{"id": kept.id, "text": "Останется"}],
            )
        ],
    )
    db.commit()

    response = get_response(db, task_id=task.id, user_id=student.id)
    assert get_selected_options(db, response_id=response.id) == {block.id: {kept.id}}


# --- галерея, скрытые вопросы, проверка ------------------------------------


def _photo(*urls):
    return {"block_type": BLOCK_PHOTO, "images": [{"url": u, "path": None} for u in urls]}


def test_gallery_keeps_order_and_caps_at_limit(db):
    from app.models.task_block import MAX_BLOCK_IMAGES

    task = _task(db)
    urls = [f"https://s3/{i}.jpg" for i in range(MAX_BLOCK_IMAGES + 3)]
    [block] = sync_blocks(db, task_id=task.id, items=[_photo(*urls)])
    db.commit()

    saved = get_images(db, [block.id])[block.id]
    assert [i.image_s3_url for i in saved] == urls[:MAX_BLOCK_IMAGES]
    assert [i.sort_order for i in saved] == list(range(MAX_BLOCK_IMAGES))


def test_switching_photo_block_to_text_drops_images(db):
    task = _task(db)
    [block] = sync_blocks(db, task_id=task.id, items=[_photo("https://s3/a.jpg")])
    db.commit()
    assert len(get_images(db, [block.id])[block.id]) == 1

    sync_blocks(db, task_id=task.id, items=[_text("Теперь текст", id=block.id)])
    db.commit()

    assert get_images(db, [block.id]) == {}


def test_hidden_question_appears_only_after_task_is_done(db):
    """Развязка тупика: скрытый вопрос не участвует в проверке «ответил ли»,
    иначе задание нельзя было бы закрыть никогда."""
    from app.services.task_blocks import visible_question_blocks

    task = _task(db)
    sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question("Виден сразу"),
            _question("Только после сдачи", hidden_until_done=True),
        ],
    )
    db.commit()
    blocks = get_blocks(db, task.id)

    assert [b.body for b in visible_question_blocks(blocks, task_done=False)] == ["Виден сразу"]
    assert [b.body for b in visible_question_blocks(blocks, task_done=True)] == [
        "Виден сразу", "Только после сдачи",
    ]


def test_grade_counts_only_questions_with_a_right_answer(db, user_factory):
    from app.services.task_blocks import grade_response

    task = _task(db)
    student = user_factory(vk_id=700_201, name="Ученик")
    verny, bez_klyucha, svobodny = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question("С ключом", question_type=QUESTION_SINGLE,
                      options=[{"text": "Да", "is_correct": True}, {"text": "Нет"}]),
            _question("Без ключа", question_type=QUESTION_SINGLE,
                      options=[{"text": "А"}, {"text": "Б"}]),
            _question("Свободный"),
        ],
    )
    db.commit()
    right = [o for o in get_options(db, [verny.id])[verny.id] if o.is_correct][0]

    save_response(
        db, task_id=task.id, user_id=student.id, blocks=[verny],
        answers={verny.id: {"option_ids": [right.id]}},
    )
    db.commit()
    response = get_response(db, task_id=task.id, user_id=student.id)

    verdict = grade_response(db, blocks=get_blocks(db, task.id), response_id=response.id)
    assert verdict["correct_count"] == 1
    assert verdict["gradable_count"] == 1  # свободный и «без ключа» не считаются
    by_block = {r["block_id"]: r["is_correct"] for r in verdict["results"]}
    assert by_block[verny.id] is True
    assert by_block[bez_klyucha.id] is None


def test_grade_multiple_requires_full_match(db, user_factory):
    from app.services.task_blocks import grade_response

    task = _task(db)
    student = user_factory(vk_id=700_202, name="Ученик")
    [block] = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question("Выбери всё верное", question_type=QUESTION_MULTIPLE,
                      options=[
                          {"text": "А", "is_correct": True},
                          {"text": "Б", "is_correct": True},
                          {"text": "В"},
                      ])
        ],
    )
    db.commit()
    a, b, v = get_options(db, [block.id])[block.id]

    # Угадал только половину — засчитывать нельзя.
    save_response(db, task_id=task.id, user_id=student.id, blocks=[block],
                  answers={block.id: {"option_ids": [a.id]}})
    db.commit()
    response = get_response(db, task_id=task.id, user_id=student.id)
    assert grade_response(db, blocks=[block], response_id=response.id)["correct_count"] == 0

    save_response(db, task_id=task.id, user_id=student.id, blocks=[block],
                  answers={block.id: {"option_ids": [a.id, b.id]}})
    db.commit()
    assert grade_response(db, blocks=[block], response_id=response.id)["correct_count"] == 1


# --- единая лента: is_required/subject/tariffs, состояние блока, доступность
# (владелец 05.09.2026) -------------------------------------------------------


def test_sync_blocks_persists_is_required_and_subject(db):
    task = _task(db)
    [block] = sync_blocks(
        db,
        task_id=task.id,
        items=[_text("Досмотри видео", is_required=True, subject="Рисунок")],
    )
    db.commit()

    assert block.is_required is True
    assert block.subject == "Рисунок"


def test_sync_blocks_is_required_defaults_false(db):
    """Регрессия: старые блоки без is_required в payload не должны внезапно
    стать обязательными — default=False, не True, как у TrackerTask."""
    task = _task(db)
    [block] = sync_blocks(db, task_id=task.id, items=[_text("Просто текст")])
    db.commit()

    assert block.is_required is False


def test_sync_blocks_rejects_unknown_subject(db):
    """Неизвестное значение предмета (не «Рисунок»/«Композиция») не должно
    попасть в базу как есть — иначе гейт по предмету тихо сломается."""
    task = _task(db)
    [block] = sync_blocks(
        db, task_id=task.id, items=[_text("Текст", subject="Танцы")]
    )
    db.commit()

    assert block.subject is None


def test_sync_blocks_persists_tariffs(db):
    task = _task(db)
    [block] = sync_blocks(
        db,
        task_id=task.id,
        items=[_text("Только максимуму", tariffs=["МАКСИМУМ"])],
    )
    db.commit()

    assert get_tariffs(db, [block.id]) == {block.id: {"МАКСИМУМ"}}


def test_sync_blocks_ignores_unknown_tariff(db):
    task = _task(db)
    [block] = sync_blocks(
        db,
        task_id=task.id,
        items=[_text("Текст", tariffs=["МАКСИМУМ", "НЕСУЩЕСТВУЮЩИЙ"])],
    )
    db.commit()

    assert get_tariffs(db, [block.id]) == {block.id: {"МАКСИМУМ"}}


def test_sync_blocks_tariffs_removed_on_resync(db):
    task = _task(db)
    [block] = sync_blocks(
        db, task_id=task.id, items=[_text("Текст", tariffs=["МАКСИМУМ"])]
    )
    db.commit()
    assert get_tariffs(db, [block.id]) == {block.id: {"МАКСИМУМ"}}

    sync_blocks(
        db,
        task_id=task.id,
        items=[{"id": block.id, **_text("Текст")}],
    )
    db.commit()

    assert get_tariffs(db, [block.id]) == {}


def test_option_requires_text_round_trip(db, user_factory):
    task = _task(db)
    student = user_factory(vk_id=700_301, name="Ученик")
    [block] = sync_blocks(
        db,
        task_id=task.id,
        items=[
            _question(
                "Какие навыки развивать?", question_type=QUESTION_MULTIPLE,
                options=[
                    {"text": "Стрессоустойчивость", "requires_text": True},
                    {"text": "Уверенность"},
                ],
            )
        ],
    )
    db.commit()
    stress, confidence = get_options(db, [block.id])[block.id]
    assert stress.requires_text is True
    assert confidence.requires_text is False

    save_response(
        db, task_id=task.id, user_id=student.id, blocks=[block],
        answers={
            block.id: {
                "option_ids": [stress.id, confidence.id],
                "option_texts": {stress.id: "Часто нервничаю перед экзаменом"},
            }
        },
    )
    db.commit()

    response = get_response(db, task_id=task.id, user_id=student.id)
    answers = {
        row.option_id: row.text
        for row in db.query(TaskBlockAnswerOption)
        .join(TaskBlockAnswer, TaskBlockAnswer.id == TaskBlockAnswerOption.answer_id)
        .filter(TaskBlockAnswer.response_id == response.id)
        .all()
    }
    assert answers[stress.id] == "Часто нервничаю перед экзаменом"
    # Текст под вариантом без requires_text молча игнорируется, даже если пришёл.
    assert answers[confidence.id] is None


def test_close_block_for_user_is_idempotent_open_to_done_only(db, regular_user):
    task = _task(db)
    [block] = sync_blocks(db, task_id=task.id, items=[_text("Видео")])
    db.commit()

    state = close_block_for_user(db, block, regular_user.id, source="auto")
    db.commit()
    assert state.status == STATUS_DONE
    first_completed_at = state.completed_at

    # Повторное закрытие тем же или другим источником не откатывает и не
    # переписывает completed_at — идемпотентно, как у close_task_for_user.
    state_again = close_block_for_user(db, block, regular_user.id, source="manual")
    db.commit()
    assert state_again.id == state.id
    assert state_again.status == STATUS_DONE
    assert state_again.completed_at == first_completed_at
    assert state_again.completion_source == "auto"


def test_close_block_for_user_creates_state_lazily(db, regular_user):
    task = _task(db)
    [block] = sync_blocks(db, task_id=task.id, items=[_text("Видео")])
    db.commit()

    assert get_state(db, block_id=block.id, user_id=regular_user.id) is None

    close_block_for_user(db, block, regular_user.id, source="auto")
    db.commit()

    state = get_state(db, block_id=block.id, user_id=regular_user.id)
    assert state is not None
    assert state.status == STATUS_DONE


def _accessible(db, blocks, user_id, user_tariff, index):
    states = get_states(db, block_ids=[b.id for b in blocks], user_id=user_id)
    tariffs_by_block = get_tariffs(db, [b.id for b in blocks])
    return is_block_accessible(
        block_index=index, blocks=blocks, states=states,
        tariffs_by_block=tariffs_by_block, user_tariff=user_tariff,
    )


def test_is_block_accessible_optional_block_never_blocks(db, regular_user):
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[_text("Первый"), _text("Второй")],  # оба не обязательны
    )
    db.commit()

    assert _accessible(db, blocks, regular_user.id, "УВЕРЕННЫЙ", 1) is True


def test_is_block_accessible_required_block_blocks_next_until_done(db, regular_user):
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[_text("Видео", is_required=True), _text("Задание")],
    )
    db.commit()
    first, second = blocks

    assert _accessible(db, blocks, regular_user.id, "УВЕРЕННЫЙ", 1) is False

    close_block_for_user(db, first, regular_user.id, source="auto")
    db.commit()

    assert _accessible(db, blocks, regular_user.id, "УВЕРЕННЫЙ", 1) is True


def test_is_block_accessible_required_block_blocks_entire_rest_of_chain(db, regular_user):
    """Один незакрытый обязательный блок блокирует всё, что после него, а не
    только непосредственно следующий (то же правило, что у TrackerTask.is_required)."""
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[_text("Первый", is_required=True), _text("Второй"), _text("Третий")],
    )
    db.commit()

    assert _accessible(db, blocks, regular_user.id, "УВЕРЕННЫЙ", 1) is False
    assert _accessible(db, blocks, regular_user.id, "УВЕРЕННЫЙ", 2) is False


def test_is_block_accessible_tariff_gate(db, user_factory):
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[_text("Только максимуму", tariffs=["МАКСИМУМ"])],
    )
    db.commit()

    maximum_student = user_factory(vk_id=700_401, tariff="МАКСИМУМ")
    confident_student = user_factory(vk_id=700_402, tariff="УВЕРЕННЫЙ")

    assert _accessible(db, blocks, maximum_student.id, "МАКСИМУМ", 0) is True
    assert _accessible(db, blocks, confident_student.id, "УВЕРЕННЫЙ", 0) is False


def test_is_block_accessible_required_block_not_for_this_tariff_does_not_block(db, user_factory):
    """Обязательный блок, недоступный этому ученику по тарифу, не должен
    вечно блокировать его дальше по ленте — тупика без выхода быть не должно."""
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[
            _text("Домашка только для максимума", is_required=True, tariffs=["МАКСИМУМ"]),
            _text("Общее для всех"),
        ],
    )
    db.commit()

    confident_student = user_factory(vk_id=700_403, tariff="УВЕРЕННЫЙ")

    assert _accessible(db, blocks, confident_student.id, "УВЕРЕННЫЙ", 1) is True


def test_is_block_accessible_bypass_sequence_flag(db, regular_user):
    """Явный флаг куратора — блок доступен сразу, не дожидаясь предыдущих
    блоков (владелец 06.09.2026), иначе ученик потеряет ссылку из виду
    (созвон 03.09). Раньше это было жёстко зашито на block_type=link, теперь
    явный флаг, который куратор проставляет сам."""
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[
            _text("Видео", is_required=True),
            {
                "block_type": BLOCK_LINK, "url": "https://meet.example.org/lesson",
                "title": "Подключиться", "bypass_sequence": True,
            },
        ],
    )
    db.commit()

    # Первый (обязательный) блок ещё не закрыт — обычный блок был бы
    # заблокирован, а этот, с флагом, всё равно доступен.
    assert _accessible(db, blocks, regular_user.id, "УВЕРЕННЫЙ", 1) is True


def test_is_block_accessible_link_without_bypass_flag_still_gated(db, regular_user):
    """Регрессия ровно на найденный конфликт (созвон 03.09): для тарифа
    «Уверенный максимум» ссылка на занятие ДОЛЖНА ждать сдачи домашки, а не
    быть исключением. Без явного bypass_sequence блок-ссылка ведёт себя как
    любой другой — жёсткая привязка к block_type убрана."""
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[
            _text("Сдай домашку", is_required=True),
            {"block_type": BLOCK_LINK, "url": "https://meet.example.org/lesson"},
        ],
    )
    db.commit()

    assert _accessible(db, blocks, regular_user.id, "УВЕРЕННЫЙ", 1) is False


def test_is_block_accessible_bypass_sequence_still_respects_own_tariff_gate(db, user_factory):
    """Исключение из последовательной блокировки — не исключение из тарифа:
    ссылка на закрытое тарифом занятие не должна течь всем подряд."""
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[{
            "block_type": BLOCK_LINK, "url": "https://meet.example.org/lesson",
            "tariffs": ["МАКСИМУМ"], "bypass_sequence": True,
        }],
    )
    db.commit()

    confident_student = user_factory(vk_id=700_404, tariff="УВЕРЕННЫЙ")

    assert _accessible(db, blocks, confident_student.id, "УВЕРЕННЫЙ", 0) is False


# --- период доступа: opens_at (владелец 03.09.2026, найдено 06.09.2026) -----


def test_is_block_accessible_respects_future_opens_at(db, regular_user):
    """«Теория и задания откроются только с 23 сентября 0000» — независимо
    от того, что ученик сделал с предыдущими блоками."""
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[_text("Откроется позже", opens_at=date(2099, 1, 1))],
    )
    db.commit()

    assert _accessible(db, blocks, regular_user.id, "УВЕРЕННЫЙ", 0) is False


def test_is_block_accessible_opens_at_in_the_past_is_accessible(db, regular_user):
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[_text("Уже открыт", opens_at=date(2020, 1, 1))],
    )
    db.commit()

    assert _accessible(db, blocks, regular_user.id, "УВЕРЕННЫЙ", 0) is True


def test_is_block_accessible_opens_at_combines_with_sequence():
    """opens_at и is_required складываются, а не заменяют друг друга:
    блок с прошедшей датой открытия всё равно ждёт очередь."""
    now = datetime(2026, 9, 6, tzinfo=timezone.utc)
    past = TaskBlock(id=1, sort_order=0, is_required=True, opens_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    later = TaskBlock(id=2, sort_order=1, opens_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    blocks = [past, later]

    assert is_block_accessible(
        block_index=1, blocks=blocks, states={}, tariffs_by_block={},
        user_tariff="УВЕРЕННЫЙ", now=now,
    ) is False  # дата открытия прошла, но предыдущий обязательный блок не закрыт


def test_sync_blocks_persists_opens_at_and_bypass_sequence(db):
    task = _task(db)
    [block] = sync_blocks(
        db, task_id=task.id,
        items=[_text("Текст", opens_at=date(2026, 9, 23), bypass_sequence=True)],
    )
    db.commit()

    assert block.bypass_sequence is True
    assert block.opens_at is not None
    # Полночь МСК 23 сентября — это 21:00 UTC 22 сентября.
    assert block.opens_at.astimezone(timezone.utc).isoformat() == "2026-09-22T21:00:00+00:00"


def test_sync_blocks_without_opens_at_leaves_it_none(db):
    task = _task(db)
    [block] = sync_blocks(db, task_id=task.id, items=[_text("Текст")])
    db.commit()

    assert block.opens_at is None
    assert block.bypass_sequence is False


def test_block_status_locked_current_done(db, regular_user):
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[_text("Первый", is_required=True), _text("Второй")],
    )
    db.commit()
    first, second = blocks

    states = get_states(db, block_ids=[first.id, second.id], user_id=regular_user.id)
    assert block_status(second, states.get(second.id), accessible=False) == "locked"
    assert block_status(first, states.get(first.id), accessible=True) == "current"

    close_block_for_user(db, first, regular_user.id, source="auto")
    db.commit()
    states = get_states(db, block_ids=[first.id, second.id], user_id=regular_user.id)
    assert block_status(first, states.get(first.id), accessible=True) == "done"


def test_feed_state_end_to_end(db, regular_user):
    task = _task(db)
    blocks = sync_blocks(
        db, task_id=task.id,
        items=[_text("Видео", is_required=True), _text("Опрос"), _text("Итог")],
    )
    db.commit()
    first, second, third = blocks

    feed = feed_state(db, task_id=task.id, user_id=regular_user.id, user_tariff="УВЕРЕННЫЙ")
    by_id = {row["block"].id: row["status"] for row in feed}
    assert by_id[first.id] == "current"
    assert by_id[second.id] == "locked"
    assert by_id[third.id] == "locked"

    close_block_for_user(db, first, regular_user.id, source="auto")
    db.commit()

    feed = feed_state(db, task_id=task.id, user_id=regular_user.id, user_tariff="УВЕРЕННЫЙ")
    by_id = {row["block"].id: row["status"] for row in feed}
    assert by_id[first.id] == "done"
    assert by_id[second.id] == "current"
    assert by_id[third.id] == "current"


def test_drop_block_cleans_up_state_and_tariffs(db, regular_user):
    """SQLite в тестах не исполняет ON DELETE CASCADE — чистка при удалении
    блока явная, как у вариантов и ответов (см. докстринг модуля)."""
    task = _task(db)
    [block] = sync_blocks(
        db, task_id=task.id, items=[_text("Текст", tariffs=["МАКСИМУМ"])]
    )
    db.commit()
    close_block_for_user(db, block, regular_user.id, source="auto")
    db.commit()

    sync_blocks(db, task_id=task.id, items=[])  # блок не пришёл в items — удалён
    db.commit()

    assert db.query(TaskBlockState).count() == 0
    assert get_tariffs(db, [block.id]) == {}
