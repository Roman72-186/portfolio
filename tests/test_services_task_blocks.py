"""Универсальный конструктор содержимого элемента дня — сервисный слой.

Держит два контракта, ради которых модуль и написан: правка списка блоков не
рвёт уже сохранённые ответы учеников, а удаление блока уносит их вместе с
собой (SQLite в тестах не исполняет ON DELETE CASCADE, чистка явная).
"""

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
)
from app.models.tracker import TrackerTask
from app.services.task_blocks import (
    get_answers_map,
    get_blocks,
    get_blocks_for_tasks,
    get_images,
    get_options,
    get_response,
    get_selected_options,
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
