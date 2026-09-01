"""Содержимое элемента дня: блоки конструктора (см. `app/models/task_block.py`).

Работает напрямую по `task_id`, без ORM-relationship к `TrackerTask` — как это
делал `task_quiz.py`, чью роль этот модуль забрал: блоки всегда читаются
свежим запросом, кэша между запросами нет.
"""

from sqlalchemy.orm import Session as DBSession

from app.models.task_block import (
    BLOCK_LINK,
    BLOCK_PHOTO,
    BLOCK_QUESTION,
    BLOCK_TYPES,
    BLOCK_VIDEO,
    MAX_BLOCK_IMAGES,
    QUESTION_TEXT,
    QUESTION_TYPES,
    TaskBlock,
    TaskBlockAnswer,
    TaskBlockAnswerOption,
    TaskBlockImage,
    TaskBlockOption,
    TaskBlockResponse,
)


def get_blocks(db: DBSession, task_id: int) -> list[TaskBlock]:
    """Блоки элемента по порядку."""
    return (
        db.query(TaskBlock)
        .filter(TaskBlock.task_id == task_id)
        .order_by(TaskBlock.sort_order, TaskBlock.id)
        .all()
    )


def get_blocks_for_tasks(db: DBSession, task_ids: list[int]) -> dict[int, list[TaskBlock]]:
    """Блоки сразу для пачки элементов — один запрос на день календаря вместо
    запроса на карточку."""
    if not task_ids:
        return {}
    rows = (
        db.query(TaskBlock)
        .filter(TaskBlock.task_id.in_(task_ids))
        .order_by(TaskBlock.task_id, TaskBlock.sort_order, TaskBlock.id)
        .all()
    )
    grouped: dict[int, list[TaskBlock]] = {}
    for row in rows:
        grouped.setdefault(row.task_id, []).append(row)
    return grouped


def get_options(db: DBSession, block_ids: list[int]) -> dict[int, list[TaskBlockOption]]:
    """Варианты ответа для блоков-вопросов, сгруппированные по блоку."""
    if not block_ids:
        return {}
    rows = (
        db.query(TaskBlockOption)
        .filter(TaskBlockOption.block_id.in_(block_ids))
        .order_by(TaskBlockOption.block_id, TaskBlockOption.sort_order, TaskBlockOption.id)
        .all()
    )
    grouped: dict[int, list[TaskBlockOption]] = {}
    for row in rows:
        grouped.setdefault(row.block_id, []).append(row)
    return grouped


def question_blocks(blocks: list[TaskBlock]) -> list[TaskBlock]:
    """Только блоки-вопросы — то, на что ученик отвечает."""
    return [block for block in blocks if block.block_type == BLOCK_QUESTION]


def _clean(value: str | None, limit: int) -> str | None:
    text = (value or "").strip()
    return text[:limit] if text else None


def _sync_options(
    db: DBSession, block: TaskBlock, items: list[dict] | None
) -> None:
    """Варианты ответа одного блока — та же id-сохраняющая логика, что у самих
    блоков: выбранные учениками варианты не должны пропадать при правке
    соседнего варианта."""
    existing = {
        option.id: option
        for option in db.query(TaskBlockOption)
        .filter(TaskBlockOption.block_id == block.id)
        .all()
    }
    matched_ids: set[int] = set()
    for order, raw in enumerate(items or []):
        text = _clean(raw.get("text"), 300)
        if not text:
            continue
        raw_id = raw.get("id")
        row = existing.get(raw_id) if raw_id is not None else None
        if row is not None:
            row.text = text
            row.is_correct = bool(raw.get("is_correct"))
            row.sort_order = order
            matched_ids.add(row.id)
        else:
            db.add(
                TaskBlockOption(
                    block_id=block.id,
                    text=text,
                    is_correct=bool(raw.get("is_correct")),
                    sort_order=order,
                )
            )
    dropped = False
    for option_id, row in existing.items():
        if option_id in matched_ids:
            continue
        # SQLite в тестах не исполняет ON DELETE CASCADE — чистим явно, иначе
        # осиротевшая строка выбранного варианта переживёт свой вариант.
        db.query(TaskBlockAnswerOption).filter(
            TaskBlockAnswerOption.option_id == option_id
        ).delete()
        db.delete(row)
        dropped = True
    if dropped:
        db.flush()
        _prune_empty_answers(db, block.id)


def _prune_empty_answers(db: DBSession, block_id: int) -> None:
    """Убрать ответы, от которых после правки вопроса ничего не осталось.

    Преподаватель удалил вариант, который ученик выбрал, — строка
    `TaskBlockAnswer` осталась бы висеть без текста и без единого выбранного
    варианта. Визуально это пустая форма, но по данным блок выглядел бы
    отвеченным, и любой будущий подсчёт ответов соврал бы.
    """
    answers = (
        db.query(TaskBlockAnswer)
        .filter(TaskBlockAnswer.block_id == block_id)
        .all()
    )
    for answer in answers:
        if (answer.text or "").strip():
            continue
        has_option = (
            db.query(TaskBlockAnswerOption.option_id)
            .filter(TaskBlockAnswerOption.answer_id == answer.id)
            .first()
        )
        if has_option is None:
            db.delete(answer)


def get_images(db: DBSession, block_ids: list[int]) -> dict[int, list[TaskBlockImage]]:
    """Картинки блоков-галерей, сгруппированные по блоку."""
    if not block_ids:
        return {}
    rows = (
        db.query(TaskBlockImage)
        .filter(TaskBlockImage.block_id.in_(block_ids))
        .order_by(TaskBlockImage.block_id, TaskBlockImage.sort_order, TaskBlockImage.id)
        .all()
    )
    grouped: dict[int, list[TaskBlockImage]] = {}
    for row in rows:
        grouped.setdefault(row.block_id, []).append(row)
    return grouped


def _sync_images(db: DBSession, block: TaskBlock, items: list[dict] | None) -> None:
    """Картинки одного блока-галереи.

    Полная пересборка, а не правка по id: у картинки нет ничего, что стоило бы
    сохранять между сохранениями формы — ни ответов учеников, ни ссылок извне.
    Файлы в S3 при этом не трогаем, как и везде в проекте: та же картинка может
    стоять в копии задания в другой неделе.
    """
    db.query(TaskBlockImage).filter(
        TaskBlockImage.block_id == block.id
    ).delete(synchronize_session=False)
    for order, raw in enumerate((items or [])[:MAX_BLOCK_IMAGES]):
        url = (raw.get("url") or "").strip()
        if not url:
            continue
        db.add(
            TaskBlockImage(
                block_id=block.id,
                image_s3_url=url[:500],
                image_s3_path=(raw.get("path") or None),
                sort_order=order,
            )
        )


def visible_question_blocks(blocks: list[TaskBlock], *, task_done: bool) -> list[TaskBlock]:
    """Вопросы, которые ученик видит прямо сейчас.

    Вопрос с `hidden_until_done` появляется только после закрытия задания. Эта
    же выборка задаёт гейт «нельзя закрыть, пока не отвечены вопросы»: считаем
    только видимые, иначе скрытый вопрос сделал бы задание незакрываемым
    навсегда (решение владельца 31.08.2026).
    """
    return [
        block
        for block in question_blocks(blocks)
        if task_done or not block.hidden_until_done
    ]


def grade_response(
    db: DBSession, *, blocks: list[TaskBlock], response_id: int | None
) -> dict:
    """Результат проверки: что верно, что нет, и счёт.

    В счёт идут только вопросы с вариантами, у которых преподаватель отметил
    хотя бы один верный. Свободный текст не проверяется машиной. Вопрос без
    отмеченного верного варианта завести нельзя (форма не даёт сохранить), но
    в базе он может остаться от старых данных — такой считаем непроверяемым,
    а не заваленным.

    У `multiple` — совпадение множеств целиком, без частичных баллов.
    """
    graded = [
        block
        for block in question_blocks(blocks)
        if block.question_type != QUESTION_TEXT
    ]
    options = get_options(db, [block.id for block in graded])
    chosen = (
        get_selected_options(db, response_id=response_id) if response_id else {}
    )
    results: list[dict] = []
    correct_count = 0
    gradable_count = 0
    for block in graded:
        right = {o.id for o in options.get(block.id, []) if o.is_correct}
        if not right:
            results.append({"block_id": block.id, "is_correct": None})
            continue
        gradable_count += 1
        is_correct = chosen.get(block.id, set()) == right
        if is_correct:
            correct_count += 1
        results.append({"block_id": block.id, "is_correct": is_correct})
    return {
        "correct_count": correct_count,
        "gradable_count": gradable_count,
        "results": results,
    }


def _drop_block(db: DBSession, block: TaskBlock) -> None:
    """Удалить блок вместе со всем, что на него ссылается.

    Файлы в S3 при этом не трогаем — ровно как `tracker.set_homework_images`:
    та же картинка может стоять в копии элемента в другой неделе, и уборка в
    хранилище ценой пустого квадрата в чужой неделе — плохая сделка.
    """
    option_ids = [
        row.id
        for row in db.query(TaskBlockOption.id)
        .filter(TaskBlockOption.block_id == block.id)
        .all()
    ]
    answer_ids = [
        row.id
        for row in db.query(TaskBlockAnswer.id)
        .filter(TaskBlockAnswer.block_id == block.id)
        .all()
    ]
    if option_ids:
        db.query(TaskBlockAnswerOption).filter(
            TaskBlockAnswerOption.option_id.in_(option_ids)
        ).delete(synchronize_session=False)
    if answer_ids:
        db.query(TaskBlockAnswerOption).filter(
            TaskBlockAnswerOption.answer_id.in_(answer_ids)
        ).delete(synchronize_session=False)
        db.query(TaskBlockAnswer).filter(
            TaskBlockAnswer.id.in_(answer_ids)
        ).delete(synchronize_session=False)
    db.query(TaskBlockOption).filter(
        TaskBlockOption.block_id == block.id
    ).delete(synchronize_session=False)
    db.delete(block)


def _is_empty(block_type: str, item: dict) -> bool:
    """Пустая заготовка — нажали «плюс» и ничего не заполнили. Такие блоки
    молча отбрасываем, как отбрасывались пустые вопросы мини-опроса."""
    if block_type == BLOCK_VIDEO:
        return item.get("video_id") is None
    if block_type == BLOCK_PHOTO:
        return not [
            image for image in (item.get("images") or [])
            if (image.get("url") or "").strip()
        ]
    if block_type == BLOCK_LINK:
        return not (item.get("url") or "").strip()
    # text и question: без текста блок бессмысленен.
    return not (item.get("body") or "").strip()


def sync_blocks(db: DBSession, *, task_id: int, items: list[dict]) -> list[TaskBlock]:
    """Развести список блоков из конструктора с уже сохранёнными в базе.

    `items` — словари в желаемом порядке; `id` присутствует у блока, который
    уже есть в базе. Правка по id, не полная пересборка: блок с тем же id
    сохраняет исходную строку, а с ней и уже сохранённые ответы учеников.
    Блок, чей id не встретился среди `items`, считается удалённым — вместе с
    ним удаляются его варианты и ответы (`_drop_block`).

    Та же id-сохраняющая логика, что была у `task_quiz.py::sync_questions` и
    есть у `video_quiz.py::sync_questions`.
    """
    existing = {
        block.id: block
        for block in db.query(TaskBlock).filter(TaskBlock.task_id == task_id).all()
    }
    matched_ids: set[int] = set()
    # Блок и его исходный payload держим парой: варианты ответа сохраняются
    # после flush (нужен block.id), а искать их обратно по позиции — способ
    # однажды приписать варианты соседнему вопросу.
    paired: list[tuple[TaskBlock, dict]] = []
    for item in items or []:
        block_type = (item.get("block_type") or "").strip()
        if block_type not in BLOCK_TYPES or _is_empty(block_type, item):
            continue
        raw_id = item.get("id")
        row = existing.get(raw_id) if raw_id is not None else None
        if row is None:
            row = TaskBlock(task_id=task_id)
            db.add(row)
        else:
            matched_ids.add(row.id)
        row.block_type = block_type
        row.sort_order = len(paired)
        row.title = _clean(item.get("title"), 200)
        row.body = (item.get("body") or "").strip() or None
        # Специализированные поля чистим у чужих типов: блок могли переключить
        # с видео на текст, и старый video_id тянул бы за собой плеер.
        row.video_id = item.get("video_id") if block_type == BLOCK_VIDEO else None
        row.url = _clean(item.get("url"), 500) if block_type == BLOCK_LINK else None
        row.hidden_until_done = bool(
            item.get("hidden_until_done") if block_type == BLOCK_QUESTION else False
        )
        if block_type == BLOCK_QUESTION:
            question_type = (item.get("question_type") or "").strip()
            row.question_type = (
                question_type if question_type in QUESTION_TYPES else QUESTION_TEXT
            )
        else:
            row.question_type = None
        paired.append((row, item))
    db.flush()
    for row, item in paired:
        # Свободный текст вариантов не имеет; смена типа вопроса на текстовый
        # или блока на не-вопрос должна убрать оставшиеся варианты.
        if row.block_type == BLOCK_QUESTION and row.question_type != QUESTION_TEXT:
            _sync_options(db, row, item.get("options") or [])
        else:
            _sync_options(db, row, [])
        # Картинки — только у галереи; блок могли переключить с фото на текст.
        _sync_images(db, row, item.get("images") if row.block_type == BLOCK_PHOTO else [])
    for block_id, row in existing.items():
        if block_id in matched_ids:
            continue
        _drop_block(db, row)
    db.flush()
    return [row for row, _ in paired]


def get_response(db: DBSession, *, task_id: int, user_id: int) -> TaskBlockResponse | None:
    return (
        db.query(TaskBlockResponse)
        .filter(
            TaskBlockResponse.task_id == task_id,
            TaskBlockResponse.user_id == user_id,
        )
        .one_or_none()
    )


def get_answers_map(db: DBSession, *, response_id: int) -> dict[int, str]:
    """Свободные ответы ученика: блок → текст."""
    return {
        answer.block_id: answer.text or ""
        for answer in db.query(TaskBlockAnswer)
        .filter(TaskBlockAnswer.response_id == response_id)
        .all()
    }


def get_selected_options(db: DBSession, *, response_id: int) -> dict[int, set[int]]:
    """Выбранные варианты: блок → множество id вариантов."""
    rows = (
        db.query(TaskBlockAnswer.block_id, TaskBlockAnswerOption.option_id)
        .join(TaskBlockAnswerOption, TaskBlockAnswerOption.answer_id == TaskBlockAnswer.id)
        .filter(TaskBlockAnswer.response_id == response_id)
        .all()
    )
    selected: dict[int, set[int]] = {}
    for block_id, option_id in rows:
        selected.setdefault(block_id, set()).add(option_id)
    return selected


def save_response(
    db: DBSession,
    *,
    task_id: int,
    user_id: int,
    blocks: list[TaskBlock],
    answers: dict[int, dict],
) -> TaskBlockResponse:
    """Сохранить ответы ученика на блоки-вопросы элемента.

    `answers` — `{block_id: {"text": str | None, "option_ids": [int]}}`.
    Идемпотентно: повторная отправка обновляет те же строки, не заводит второе
    заполнение (уникальность по task_id + user_id).
    """
    response = get_response(db, task_id=task_id, user_id=user_id)
    if response is None:
        response = TaskBlockResponse(task_id=task_id, user_id=user_id)
        db.add(response)
        db.flush()
    existing = {
        answer.block_id: answer
        for answer in db.query(TaskBlockAnswer)
        .filter(TaskBlockAnswer.response_id == response.id)
        .all()
    }
    allowed_options = get_options(db, [block.id for block in blocks])
    for block in blocks:
        payload = answers.get(block.id)
        if payload is None:
            continue
        answer = existing.get(block.id)
        text = (payload.get("text") or "").strip() or None
        if answer is None:
            answer = TaskBlockAnswer(
                response_id=response.id, block_id=block.id, text=text
            )
            db.add(answer)
            db.flush()
        else:
            answer.text = text
            db.query(TaskBlockAnswerOption).filter(
                TaskBlockAnswerOption.answer_id == answer.id
            ).delete(synchronize_session=False)
        if block.question_type == QUESTION_TEXT:
            continue
        # Принимаем только варианты этого блока: id из чужого блока в теле
        # запроса не должен попасть в ответ.
        valid = {option.id for option in allowed_options.get(block.id, [])}
        for option_id in payload.get("option_ids") or []:
            if option_id in valid:
                db.add(
                    TaskBlockAnswerOption(answer_id=answer.id, option_id=option_id)
                )
    db.flush()
    return response


# --- очередь проверки -------------------------------------------------------


def review_queue(
    db: DBSession,
    *,
    curator_id: int | None = None,
    only_unreviewed: bool = True,
    subject: str | None = None,
    student_id: int | None = None,
    tariff: str | None = None,
    week_start: "datetime | None" = None,
    week_end: "datetime | None" = None,
    limit: int = 200,
) -> list[dict]:
    """Очередь проверки: ответы учеников, свежие сверху.

    Владелец 31.08.2026 попросил показывать **все** вопросы, а не только
    свободные: видно должно быть сам вопрос, что ученик выбрал и что написал.
    Проверяемые машиной всё равно попадают в очередь — преподаватель хочет
    видеть, кто именно споткнулся.

    `curator_id` — ограничение куратора своими учениками (тот же приём, что в
    `cabinet_students_shared.py::_get_accessible_students`). Главный
    преподаватель и суперадмин зовут без него и видят всех.

    `week_start`/`week_end` фильтруют по `TaskBlockResponse.updated_at` — дате
    сдачи ответа, а не по дедлайну `TrackerTask.due_at` (решение владельца
    01.09.2026, вопрос 3: «неделя по дате сдачи/создания записи»). Дедлайн у
    задания может быть не проставлен вовсе — тогда фильтр по нему не находил
    бы ответ ни в одной неделе (нашлось 02.09.2026 при сносе `/cabinet/staff
    /review`, там `review_queue` звался вообще без периода).

    Один запрос с join'ами вместо чтения по строке: экран на два-три десятка
    учеников иначе дал бы сотни походов в базу.
    """
    from app.models.tracker import TrackerTask
    from app.models.user import User

    q = (
        db.query(TaskBlockAnswer, TaskBlock, TaskBlockResponse, TrackerTask, User)
        .join(TaskBlockResponse, TaskBlockResponse.id == TaskBlockAnswer.response_id)
        .join(TaskBlock, TaskBlock.id == TaskBlockAnswer.block_id)
        .join(TrackerTask, TrackerTask.id == TaskBlockResponse.task_id)
        .join(User, User.id == TaskBlockResponse.user_id)
        .filter(TrackerTask.deleted_at.is_(None))
    )
    if only_unreviewed:
        q = q.filter(TaskBlockAnswer.reviewed_at.is_(None))
    if curator_id is not None:
        q = q.filter(User.curator_id == curator_id)
    if student_id is not None:
        q = q.filter(User.id == student_id)
    if subject:
        q = q.filter(TrackerTask.subject == subject)
    if tariff:
        q = q.filter(User.tariff == tariff)
    if week_start is not None:
        q = q.filter(TaskBlockResponse.updated_at >= week_start)
    if week_end is not None:
        q = q.filter(TaskBlockResponse.updated_at < week_end)

    rows = q.order_by(TaskBlockResponse.updated_at.desc(), TaskBlockAnswer.id.desc()).limit(limit).all()
    if not rows:
        return []

    options = get_options(db, [block.id for _a, block, _r, _t, _u in rows])
    chosen = {}
    for answer, _b, response, _t, _u in rows:
        chosen.setdefault(response.id, None)
    for response_id in list(chosen):
        chosen[response_id] = get_selected_options(db, response_id=response_id)

    items: list[dict] = []
    for answer, block, response, task, student in rows:
        picked = chosen.get(response.id, {}).get(block.id, set())
        items.append({
            "answer_id": answer.id,
            "student_id": student.id,
            "student_name": student.name,
            "task_id": task.id,
            "task_title": task.title,
            "subject": task.subject,
            "question": block.body or "",
            "question_type": block.question_type,
            "text": answer.text or "",
            "chosen": [o.text for o in options.get(block.id, []) if o.id in picked],
            "correct": [o.text for o in options.get(block.id, []) if o.is_correct],
            "reviewed": answer.reviewed_at is not None,
            "answered_at": response.updated_at,
        })
    return items


def set_reviewed(
    db: DBSession, *, answer_id: int, user_id: int, reviewed: bool
) -> TaskBlockAnswer | None:
    """Отметить ответ просмотренным или снять отметку.

    Снимать может тот же staff — ткнули случайно, надо уметь вернуть
    (владелец 31.08.2026). Ученик отметку видит, но не трогает.
    """
    answer = db.get(TaskBlockAnswer, answer_id)
    if answer is None:
        return None
    if reviewed:
        answer.reviewed_at = answer.reviewed_at or _now()
        answer.reviewed_by_id = user_id
    else:
        answer.reviewed_at = None
        answer.reviewed_by_id = None
    db.flush()
    return answer


def _now():
    from app.services.tz import now_msk
    return now_msk()
