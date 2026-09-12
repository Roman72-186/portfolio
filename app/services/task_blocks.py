"""Содержимое элемента дня: блоки конструктора (см. `app/models/task_block.py`).

Работает напрямую по `task_id`, без ORM-relationship к `TrackerTask` — как это
делал `task_quiz.py`, чью роль этот модуль забрал: блоки всегда читаются
свежим запросом, кэша между запросами нет.
"""

from datetime import date as date_type, timezone

from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS, TARIFFS
from app.models.task_block import (
    BLOCK_LINK,
    BLOCK_PHOTO,
    BLOCK_PHOTO_UPLOAD,
    BLOCK_PORTFOLIO,
    BLOCK_RULES,
    BLOCK_SCALE,
    BLOCK_TIMED,
    BLOCK_UPLOAD,
    BLOCK_QUESTION,
    BLOCK_TYPE_LABELS,
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
    TaskBlockRequiredTariff,
    TaskBlockResponse,
    TaskBlockState,
    TaskBlockSubmission,
    TaskBlockSubmissionImage,
    TaskBlockTariff,
)
from app.models.tracker import STATUS_DONE, STATUS_OPEN
from app.services.tz import msk_midnight, parse_msk_local


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
    """Блоки, на которые ученик отвечает: вопросы, шкала навыков, правила.

    Шкала попала сюда, потому что она сохраняется тем же путём, что и вопрос
    (`save_response`), и её ответы так же участвуют в «ответил ли ученик».
    Вердикта «верно/неверно» у неё нет — оценивать самооценку не по чему.
    Правила — тем же путём, но закрываются только по всем галочкам сразу.
    """
    return [
        block for block in blocks
        if block.block_type in (BLOCK_QUESTION, BLOCK_SCALE, BLOCK_RULES)
    ]


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
        # Описание и подписи краёв — только у BLOCK_SCALE (владелец 11.09.2026,
        # анкета «Метакомпетенции»). У вопроса/правил фронт эти ключи не шлёт,
        # `.get()` тогда даёт None — то же nullable-поведение, что у
        # requires_text для не-вопросных типов.
        description = _clean(raw.get("description"), 5000)
        scale_min_label = _clean(raw.get("scale_min_label"), 200)
        scale_max_label = _clean(raw.get("scale_max_label"), 200)
        raw_id = raw.get("id")
        row = existing.get(raw_id) if raw_id is not None else None
        if row is not None:
            row.text = text
            row.is_correct = bool(raw.get("is_correct"))
            row.requires_text = bool(raw.get("requires_text"))
            row.sort_order = order
            row.description = description
            row.scale_min_label = scale_min_label
            row.scale_max_label = scale_max_label
            matched_ids.add(row.id)
        else:
            db.add(
                TaskBlockOption(
                    block_id=block.id,
                    text=text,
                    is_correct=bool(raw.get("is_correct")),
                    requires_text=bool(raw.get("requires_text")),
                    sort_order=order,
                    description=description,
                    scale_min_label=scale_min_label,
                    scale_max_label=scale_max_label,
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


def get_tariffs(db: DBSession, block_ids: list[int]) -> dict[int, set[str]]:
    """Тарифы блока, сгруппированные по блоку. Пустой набор = доступен всем."""
    if not block_ids:
        return {}
    rows = (
        db.query(TaskBlockTariff)
        .filter(TaskBlockTariff.block_id.in_(block_ids))
        .all()
    )
    grouped: dict[int, set[str]] = {}
    for row in rows:
        grouped.setdefault(row.block_id, set()).add(row.tariff)
    return grouped


def _sync_tariffs(db: DBSession, block: TaskBlock, tariffs: list[str] | None) -> None:
    """Полная пересборка списка тарифов блока.

    Как у картинок галереи — нет ничего промежуточного (ответов учеников,
    внешних ссылок), что стоило бы сохранять между правками формы, поэтому
    проще снести и собрать заново, чем разводить по id.

    Неизвестное значение (не из `app.constants.TARIFFS`) молча отбрасывается,
    а не роняет сохранение всего блока: справочник тарифов может пополниться
    или переименоваться (владелец 05.09.2026 — быстрое переименование тарифа
    это правка одной строки в `constants.py`, форма конструктора не должна
    стать вторым местом, которое надо обновлять вручную).
    """
    db.query(TaskBlockTariff).filter(
        TaskBlockTariff.block_id == block.id
    ).delete(synchronize_session=False)
    seen: set[str] = set()
    for raw in tariffs or []:
        tariff = (raw or "").strip().upper()
        if tariff not in TARIFFS or tariff in seen:
            continue
        seen.add(tariff)
        db.add(TaskBlockTariff(block_id=block.id, tariff=tariff))


def get_required_tariffs(db: DBSession, block_ids: list[int]) -> dict[int, set[str]]:
    """Тарифы, которым обязательно выполнение блока. Пустой набор = обязательно
    всем, кому блок виден (владелец 10.09.2026 — отдельная ось от видимости)."""
    if not block_ids:
        return {}
    rows = (
        db.query(TaskBlockRequiredTariff)
        .filter(TaskBlockRequiredTariff.block_id.in_(block_ids))
        .all()
    )
    grouped: dict[int, set[str]] = {}
    for row in rows:
        grouped.setdefault(row.block_id, set()).add(row.tariff)
    return grouped


def _sync_required_tariffs(
    db: DBSession, block: TaskBlock, tariffs: list[str] | None
) -> None:
    """Полная пересборка списка «кого обязать» — копия `_sync_tariffs`, та же
    причина сноса-и-пересборки и молчаливого отбрасывания неизвестного тарифа."""
    db.query(TaskBlockRequiredTariff).filter(
        TaskBlockRequiredTariff.block_id == block.id
    ).delete(synchronize_session=False)
    seen: set[str] = set()
    for raw in tariffs or []:
        tariff = (raw or "").strip().upper()
        if tariff not in TARIFFS or tariff in seen:
            continue
        seen.add(tariff)
        db.add(TaskBlockRequiredTariff(block_id=block.id, tariff=tariff))


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
        # Шкала навыков — самооценка, верного ответа у неё нет по устройству.
        if block.block_type == BLOCK_QUESTION and block.question_type != QUESTION_TEXT
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
    db.query(TaskBlockTariff).filter(
        TaskBlockTariff.block_id == block.id
    ).delete(synchronize_session=False)
    db.query(TaskBlockState).filter(
        TaskBlockState.block_id == block.id
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
    if block_type == BLOCK_TIMED:
        # Кнопка «Начать» самодостаточна, как и «Загрузить портфолио».
        return False
    if block_type in (BLOCK_SCALE, BLOCK_RULES):
        return not [
            option for option in (item.get("options") or [])
            if (option.get("text") or "").strip()
        ]
    if block_type in (BLOCK_PORTFOLIO, BLOCK_UPLOAD, BLOCK_PHOTO_UPLOAD):
        # Кнопки «Загрузить портфолио» и «Загрузить работы» самодостаточны:
        # заголовок и пояснение необязательны, своего содержимого у них нет.
        # BLOCK_PHOTO_UPLOAD туда же — фото-задание необязательно (куратор
        # мог сперва написать пояснение и вернуться за фото позже), а форма
        # приёма работы сама по себе уже содержимое.
        return False
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
        row.is_required = bool(item.get("is_required"))
        subject = _clean(item.get("subject"), 50)
        row.subject = subject if subject in MOCK_SUBJECTS else None
        row.bypass_sequence = bool(item.get("bypass_sequence"))
        # Лимит — только у работы на время; смена типа блока его убирает.
        limit = item.get("time_limit_minutes")
        row.time_limit_minutes = (
            int(limit) if block_type == BLOCK_TIMED and limit else None
        )
        opens_at_date = item.get("opens_at")
        row.opens_at = (
            msk_midnight(opens_at_date).astimezone(timezone.utc)
            if isinstance(opens_at_date, date_type) else None
        )
        row.closes_at = parse_msk_local(item.get("closes_at"))
        # Закрытие раньше открытия — куратор перепутал поля; отбрасываем
        # молча, как и везде в этой функции с некорректным вводом, а не
        # роняем сохранение всего блока (см. неизвестный тариф/предмет выше).
        if (
            row.closes_at is not None
            and row.opens_at is not None
            and row.closes_at <= row.opens_at
        ):
            row.closes_at = None
        row.locked_message = _clean(item.get("locked_message"), 300)
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
        # Варианты есть у вопроса с выбором и у шкалы навыков: там вариант —
        # это название навыка, который ученик оценивает.
        if (
            row.block_type in (BLOCK_SCALE, BLOCK_RULES)
            or (row.block_type == BLOCK_QUESTION and row.question_type != QUESTION_TEXT)
        ):
            _sync_options(db, row, item.get("options") or [])
        else:
            _sync_options(db, row, [])
        # Картинки — у галереи и у комбинированного «Фото + сдача работы»;
        # блок могли переключить с фото на текст.
        _sync_images(
            db, row,
            item.get("images") if row.block_type in (BLOCK_PHOTO, BLOCK_PHOTO_UPLOAD) else [],
        )
        _sync_tariffs(db, row, item.get("tariffs"))
        _sync_required_tariffs(db, row, item.get("required_tariffs"))
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


def get_selected_option_texts(db: DBSession, *, response_id: int) -> dict[int, str]:
    """Свободный текст под выбранными вариантами: option_id → текст.

    Для префилла формы при повторном открытии — тот же case, что уже есть у
    `get_answers_map` для текста всего вопроса, только на уровне варианта
    (владелец 05.09.2026, requires_text)."""
    rows = (
        db.query(TaskBlockAnswerOption.option_id, TaskBlockAnswerOption.text)
        .join(TaskBlockAnswer, TaskBlockAnswer.id == TaskBlockAnswerOption.answer_id)
        .filter(TaskBlockAnswer.response_id == response_id, TaskBlockAnswerOption.text.isnot(None))
        .all()
    )
    return {option_id: text for option_id, text in rows}


def save_response(
    db: DBSession,
    *,
    task_id: int,
    user_id: int,
    blocks: list[TaskBlock],
    answers: dict[int, dict],
) -> TaskBlockResponse:
    """Сохранить ответы ученика на блоки-вопросы элемента.

    `answers` — `{block_id: {"text": str | None, "option_ids": [int],
    "option_texts": {option_id: str}}}`. `option_texts` — только для
    вариантов с `TaskBlockOption.requires_text=True` (владелец 05.09.2026:
    «выбрал навык — сразу под ним раскрывается поле, почему»); для
    остальных вариантов текст молча игнорируется, даже если пришёл.

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
        if block.block_type == BLOCK_RULES:
            _save_rules(
                db,
                block=block,
                response_id=response.id,
                user_id=user_id,
                existing=existing.get(block.id),
                option_ids=payload.get("option_ids") or [],
                allowed=allowed_options.get(block.id, []),
            )
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
            _close_answered_block(db, block=block, user_id=user_id, filled=bool(text))
            continue
        # Принимаем только варианты этого блока: id из чужого блока в теле
        # запроса не должен попасть в ответ.
        options_by_id = {option.id: option for option in allowed_options.get(block.id, [])}
        option_texts = payload.get("option_texts") or {}
        accepted = 0
        for option_id in payload.get("option_ids") or []:
            option = options_by_id.get(option_id)
            if option is None:
                continue
            option_text = None
            if option.requires_text or block.block_type == BLOCK_SCALE:
                # У шкалы текст варианта — это оценка навыка («7 из 10»),
                # поэтому она принимается без флага requires_text.
                option_text = (option_texts.get(option_id) or "").strip() or None
            db.add(
                TaskBlockAnswerOption(
                    answer_id=answer.id, option_id=option_id, text=option_text
                )
            )
            accepted += 1
        _close_answered_block(
            db, block=block, user_id=user_id, filled=bool(text or accepted)
        )
    db.flush()
    return response


def _save_rules(
    db: DBSession,
    *,
    block: TaskBlock,
    response_id: int,
    user_id: int,
    existing: TaskBlockAnswer | None,
    option_ids: list[int],
    allowed: list[TaskBlockOption],
) -> None:
    """Согласие с правилами: шаг закрывается, только когда отмечены все.

    Частичная отметка **не сохраняется вовсе**, и это главное решение здесь.
    Если записать три галочки из пяти, `answered_block_ids` посчитает блок
    отвеченным (у него появится строка варианта), форма отправки исчезнет —
    и обязательный блок навсегда запрёт хвост ленты. Ровно та поломка, что
    чинилась 07.09.2026 у вопросов; повторять её новым типом не будем.

    Пока отмечено не всё, блок остаётся неотвеченным: ученик видит форму и
    доставляет галочки. Цена — недоотмеченные пункты не переживают
    перезагрузку страницы, и это дешевле запертой ленты.
    """
    allowed_ids = {option.id for option in allowed}
    chosen = {option_id for option_id in option_ids if option_id in allowed_ids}
    if not allowed_ids or chosen != allowed_ids:
        return
    answer = existing
    if answer is None:
        answer = TaskBlockAnswer(response_id=response_id, block_id=block.id, text=None)
        db.add(answer)
        db.flush()
    else:
        db.query(TaskBlockAnswerOption).filter(
            TaskBlockAnswerOption.answer_id == answer.id
        ).delete(synchronize_session=False)
    for option_id in sorted(chosen):
        db.add(TaskBlockAnswerOption(answer_id=answer.id, option_id=option_id, text=None))
    close_block_for_user(db, block=block, user_id=user_id, source="answer")


def _close_answered_block(
    db: DBSession, *, block: TaskBlock, user_id: int, filled: bool
) -> None:
    """Ответ закрывает блок (найдено 07.09.2026).

    Без этого обязательный вопрос запирал ленту навсегда: ученик отвечал,
    ответ сохранялся, состояния блока не появлялось — и `is_block_accessible`
    продолжал держать закрытым весь хвост ниже. Закрываем в сервисе, а не в
    роуте: это owner-слой «ученик ответил на блок», и другая точка входа
    получит то же поведение без своей копии правила.

    `filled` считается **после** разбора вариантов: id чужого блока из тела
    запроса отбрасывается, и такой «ответ» не должен ничего закрывать.
    """
    if filled:
        close_block_for_user(db, block=block, user_id=user_id, source="answer")


def answered_block_ids(db: DBSession, *, response_id: int) -> set[int]:
    """Блоки, на которые у ученика есть непустой ответ.

    Пустая строка и ответ без единого варианта не считаются: иначе форма
    закрылась бы у вопроса, который ученик пропустил, и он не смог бы
    вернуться (`submit_endpoint` выдаётся только по неотвеченным).
    """
    answered: set[int] = set()
    rows = (
        db.query(TaskBlockAnswer)
        .filter(TaskBlockAnswer.response_id == response_id)
        .all()
    )
    if not rows:
        return answered
    with_options = {
        row.answer_id
        for row in db.query(TaskBlockAnswerOption.answer_id)
        .filter(TaskBlockAnswerOption.answer_id.in_([r.id for r in rows]))
        .all()
    }
    for row in rows:
        if (row.text or "").strip() or row.id in with_options:
            answered.add(row.block_id)
    return answered


# --- единая лента: состояние блока, доступность, сборка (владелец 05.09.2026,
# plans/2026-09-04-apparchi-precourse-block-feed-implementation-plan.md) -----


def get_state(db: DBSession, *, block_id: int, user_id: int) -> TaskBlockState | None:
    return (
        db.query(TaskBlockState)
        .filter(TaskBlockState.block_id == block_id, TaskBlockState.user_id == user_id)
        .one_or_none()
    )


def get_states(db: DBSession, *, block_ids: list[int], user_id: int) -> dict[int, TaskBlockState]:
    """Состояния сразу для пачки блоков одного ученика — один запрос на ленту."""
    if not block_ids:
        return {}
    rows = (
        db.query(TaskBlockState)
        .filter(TaskBlockState.block_id.in_(block_ids), TaskBlockState.user_id == user_id)
        .all()
    )
    return {row.block_id: row for row in rows}


def close_block_for_user(
    db: DBSession, block: TaskBlock, user_id: int, *, source: str
) -> TaskBlockState:
    """Идемпотентно закрыть блок по событию-источнику (видео досмотрено,
    портфолио загружено и т.п.). Зеркало `tracker.close_task_for_user`.

    Только open → done, никогда наоборот: система не должна откатывать блок,
    который уже закрыт — ни повторным heartbeat'ом, ни повторным вызовом
    того же источника.
    """
    state = get_state(db, block_id=block.id, user_id=user_id)
    if state is None:
        state = TaskBlockState(block_id=block.id, user_id=user_id, status=STATUS_OPEN)
        db.add(state)
    if state.status != STATUS_DONE:
        state.status = STATUS_DONE
        state.completed_at = _now()
        state.completed_by_id = None
        state.completion_source = source
    db.flush()
    return state


def is_block_accessible(
    *,
    block_index: int,
    blocks: list[TaskBlock],
    states: dict[int, TaskBlockState],
    tariffs_by_block: dict[int, set[str]],
    user_tariff: str | None,
    required_tariffs_by_block: dict[int, set[str]] | None = None,
    now=None,
) -> bool:
    """Доступен ли ученику блок `blocks[block_index]` прямо сейчас.

    Четыре независимых условия, все должны выполняться разом:

    1. **Открытие по календарю** (владелец 03.09.2026, найдено при
       повторном разборе созвона 06.09.2026): если у блока проставлен
       `opens_at` и это время ещё не наступило — блок недоступен, независимо
       ни от чего остального. Открывается по календарю, а не по действию
       ученика: «теория и задания откроются только с 23 сентября 0000» — это
       отдельный гейт от обязательности, они складываются.
    2. **Закрытие по календарю** (владелец 10.09.2026): если у блока
       проставлен `closes_at` и этот момент уже прошёл — блок недоступен,
       так же безусловно, как и до открытия. Симметрично пункту 1, но
       отдельным полем: у блока может быть только открытие, только закрытие,
       оба сразу или ни одного.
    3. **Тариф**: недоступен, если тариф ученика не входит в список тарифов
       блока (пустой список — доступен всем).
    4. **Последовательность** (владелец 05.09.2026, подтверждено
       06.09.2026): недоступен, если среди блоков строго перед ним есть
       хотя бы один обязательный, ещё не закрытый этим учеником. Один
       незакрытый обязательный блок блокирует **весь хвост ленты**, а не
       только следующий блок — то же правило, что уже действует у
       `TrackerTask.is_required` для вкладок недели (decisions.md 23.08),
       просто на уровень ниже: не вкладка, а блок внутри неё. Обязательный
       блок, который сам недоступен этому ученику по тарифу **или уже
       закрылся по календарю** (владелец 10.09.2026 — тот же тупик, что и с
       тарифом: требовать выполнения того, что закрылось навсегда, невозможно
       в принципе), никого не блокирует. Отдельно от видимости: если у
       обязательного блока проставлен `required_tariffs` (владелец
       10.09.2026 — «на дешёвом тарифе ученик всё делает сам, на топовом
       сдача обязательна, но блок виден обоим») и тариф ученика в него не
       входит — блок для этого ученика необязателен, тоже не блокирует, хотя
       остаётся видимым.

    `target.bypass_sequence` пропускает только пункт 4, не 1, 2 и 3
    (владелец 06.09.2026). Раньше это было жёстко зашито на `BLOCK_LINK`
    («ссылка на занятие видна сразу, не дожидаясь предыдущих блоков») — но
    тот же созвон 03.09 требует обратного для тарифа «Уверенный максимум»:
    та же ссылка должна ждать сдачи домашки. Явный флаг снимает конфликт без
    ветвления по тарифу в коде: куратор делает две версии блока-ссылки —
    одну с `bypass_sequence=True` без тарифа (видна всем сразу), другую с
    `bypass_sequence=False` и тарифом `["МАКСИМУМ"]` (обычная очередь).

    Видимость: блок, недоступный по тарифу, в готовой ленте не показывается
    вообще, а не серым «недоступно на вашем тарифе» (владелец 06.09.2026) —
    решение для шаблона ленты, когда он появится; этой функции оно не
    касается, она уже возвращает чистый True/False.
    """
    moment = now or _now()
    target = blocks[block_index]
    opens_at = target.opens_at
    if opens_at is not None:
        if opens_at.tzinfo is None:
            opens_at = opens_at.replace(tzinfo=timezone.utc)
        if opens_at > moment:
            return False
    closes_at = target.closes_at
    if closes_at is not None:
        if closes_at.tzinfo is None:
            closes_at = closes_at.replace(tzinfo=timezone.utc)
        if closes_at <= moment:
            return False
    target_tariffs = tariffs_by_block.get(target.id)
    if target_tariffs and user_tariff not in target_tariffs:
        return False
    if target.bypass_sequence:
        return True
    for prior in blocks[:block_index]:
        if not prior.is_required:
            continue
        prior_tariffs = tariffs_by_block.get(prior.id)
        if prior_tariffs and user_tariff not in prior_tariffs:
            continue
        prior_required_tariffs = (
            required_tariffs_by_block.get(prior.id) if required_tariffs_by_block else None
        )
        if prior_required_tariffs and user_tariff not in prior_required_tariffs:
            continue
        prior_closes_at = prior.closes_at
        if prior_closes_at is not None:
            prior_closes = (
                prior_closes_at if prior_closes_at.tzinfo
                else prior_closes_at.replace(tzinfo=timezone.utc)
            )
            if prior_closes <= moment:
                continue
        state = states.get(prior.id)
        if state is None or state.status != STATUS_DONE:
            return False
    return True


def start_timed_block(db: DBSession, *, block: TaskBlock, user_id: int) -> TaskBlockState:
    """Отметить старт работы на время. Повторный вызов ничего не сдвигает.

    Иначе ученик, дважды нажавший «Начать», обнулял бы себе отсчёт — а он и
    есть предмет измерения (владелец 03.09.2026: «будем отслеживать, сколько
    детей превысили время»).
    """
    state = get_state(db, block_id=block.id, user_id=user_id)
    if state is None:
        state = TaskBlockState(block_id=block.id, user_id=user_id, status=STATUS_OPEN)
        db.add(state)
        db.flush()
    if state.started_at is None:
        state.started_at = _now()
        db.flush()
    return state


def timed_overrun(block: TaskBlock, state: TaskBlockState | None) -> bool:
    """Не уложился ли ученик в лимит. Без старта, лимита или сдачи — нет.

    Превышение не мешает сдать работу: «придётся делать так, что ребёнок будет
    рисовать, как у него получилось, и будет скидывать» — оно только попадает
    в статистику.
    """
    if state is None or block.time_limit_minutes is None:
        return False
    if state.started_at is None or state.completed_at is None:
        return False
    started = state.started_at
    finished = state.completed_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return (finished - started).total_seconds() > block.time_limit_minutes * 60


def block_status(
    block: TaskBlock, state: TaskBlockState | None, *, accessible: bool
) -> str:
    """`"locked"` | `"current"` | `"done"` — для рендера ленты."""
    if not accessible:
        return "locked"
    if state is not None and state.status == STATUS_DONE:
        return "done"
    return "current"


def feed_state(
    db: DBSession, *, task_id: int, user_id: int, user_tariff: str | None
) -> list[dict]:
    """Блоки задачи одним запросом + статус для конкретного ученика.

    Возвращает список `{"block": TaskBlock, "status": str, "state":
    TaskBlockState | None}` в порядке `sort_order` — ровно то, что нужно
    шаблону единой ленты для рендера, без похода в базу на каждый блок.
    """
    blocks = get_blocks(db, task_id)
    block_ids = [block.id for block in blocks]
    states = get_states(db, block_ids=block_ids, user_id=user_id)
    tariffs_by_block = get_tariffs(db, block_ids)
    now = _now()  # один и тот же момент для всех блоков ленты, не по одному на блок
    result: list[dict] = []
    for index, block in enumerate(blocks):
        accessible = is_block_accessible(
            block_index=index,
            blocks=blocks,
            states=states,
            tariffs_by_block=tariffs_by_block,
            user_tariff=user_tariff,
            now=now,
        )
        state = states.get(block.id)
        result.append({
            "block": block,
            "status": block_status(block, state, accessible=accessible),
            "state": state,
        })
    return result


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


# ── Сдача работ в блоке (владелец 07.09.2026) ────────────────────────────────

def get_submission(
    db: DBSession, *, block_id: int, user_id: int
) -> TaskBlockSubmission | None:
    return (
        db.query(TaskBlockSubmission)
        .filter(
            TaskBlockSubmission.block_id == block_id,
            TaskBlockSubmission.user_id == user_id,
        )
        .one_or_none()
    )


def get_submissions(
    db: DBSession, *, block_ids: list[int], user_id: int
) -> dict[int, TaskBlockSubmission]:
    """Сдачи сразу по пачке блоков одного ученика — один запрос на ленту."""
    if not block_ids:
        return {}
    rows = (
        db.query(TaskBlockSubmission)
        .filter(
            TaskBlockSubmission.block_id.in_(block_ids),
            TaskBlockSubmission.user_id == user_id,
        )
        .all()
    )
    return {row.block_id: row for row in rows}


def get_or_create_submission(
    db: DBSession, *, block: TaskBlock, user_id: int
) -> TaskBlockSubmission:
    submission = get_submission(db, block_id=block.id, user_id=user_id)
    if submission is None:
        submission = TaskBlockSubmission(block_id=block.id, user_id=user_id)
        db.add(submission)
        db.flush()
    return submission


def count_submission_images(db: DBSession, submission_id: int) -> int:
    return (
        db.query(TaskBlockSubmissionImage)
        .filter(TaskBlockSubmissionImage.submission_id == submission_id)
        .count()
    )


def list_submission_images(
    db: DBSession, submission_id: int
) -> list[TaskBlockSubmissionImage]:
    return (
        db.query(TaskBlockSubmissionImage)
        .filter(TaskBlockSubmissionImage.submission_id == submission_id)
        .order_by(TaskBlockSubmissionImage.sort_order, TaskBlockSubmissionImage.id)
        .all()
    )


def add_submission_image(
    db: DBSession, *, submission: TaskBlockSubmission, url: str, path: str | None
) -> TaskBlockSubmissionImage:
    image = TaskBlockSubmissionImage(
        submission_id=submission.id,
        image_s3_url=url,
        image_s3_path=path,
        sort_order=count_submission_images(db, submission.id),
    )
    db.add(image)
    db.flush()
    return image


def mark_submitted(
    db: DBSession, *, submission: TaskBlockSubmission, comment: str | None = None
) -> TaskBlockSubmission:
    """Отметить работу сданной. Пересдача до проверки сдвигает `submitted_at`
    и снимает отметку проверки — куратор должен увидеть новую версию работы,
    а не старую галочку (тот же смысл, что `needs_revision` у `Work`)."""
    submission.submitted_at = _now()
    if comment is not None:
        submission.comment = comment or None
    submission.reviewed_at = None
    submission.reviewed_by_id = None
    db.flush()
    return submission


def set_submission_reviewed(
    db: DBSession, *, submission_id: int, user_id: int, reviewed: bool,
    comment: str | None = None,
) -> TaskBlockSubmission | None:
    """Отметка куратора «работа проверена». Снимается тем же способом, что у
    ответов на вопросы (`set_reviewed`) — ткнули случайно, надо уметь вернуть."""
    submission = db.get(TaskBlockSubmission, submission_id)
    if submission is None:
        return None
    if reviewed:
        submission.reviewed_at = submission.reviewed_at or _now()
        submission.reviewed_by_id = user_id
    else:
        submission.reviewed_at = None
        submission.reviewed_by_id = None
    if comment is not None:
        submission.review_comment = comment or None
    db.flush()
    return submission


def submission_review_queue(
    db: DBSession,
    *,
    curator_id: int | None = None,
    student_id: int | None = None,
    subject: str | None = None,
    tariff: str | None = None,
    week_start=None,
    week_end=None,
    limit: int = 200,
) -> list[dict]:
    """Сданные в блоках работы для экрана проверки по ученику.

    Скоуп куратора и набор фильтров — те же, что у `review_queue` по ответам:
    оба списка сводит один агрегатор (`services/review_aggregate.py`), и
    расхождение в правилах доступа между ними было бы дырой.
    """
    from app.models.tracker import TrackerTask
    from app.models.user import User

    query = (
        db.query(TaskBlockSubmission, TaskBlock, TrackerTask, User)
        .join(TaskBlock, TaskBlock.id == TaskBlockSubmission.block_id)
        .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
        .join(User, User.id == TaskBlockSubmission.user_id)
        .filter(TaskBlockSubmission.submitted_at.isnot(None))
    )
    if curator_id is not None:
        query = query.filter(User.curator_id == curator_id)
    if student_id is not None:
        query = query.filter(TaskBlockSubmission.user_id == student_id)
    if subject:
        query = query.filter(TaskBlock.subject == subject)
    if tariff:
        query = query.filter(User.tariff == tariff)
    if week_start is not None:
        query = query.filter(TaskBlockSubmission.submitted_at >= week_start)
    if week_end is not None:
        query = query.filter(TaskBlockSubmission.submitted_at <= week_end)

    rows = (
        query.order_by(TaskBlockSubmission.submitted_at.desc())
        .limit(limit)
        .all()
    )
    image_map: dict[int, list[TaskBlockSubmissionImage]] = {}
    ids = [submission.id for submission, _, _, _ in rows]
    if ids:
        for image in (
            db.query(TaskBlockSubmissionImage)
            .filter(TaskBlockSubmissionImage.submission_id.in_(ids))
            .order_by(TaskBlockSubmissionImage.sort_order, TaskBlockSubmissionImage.id)
            .all()
        ):
            image_map.setdefault(image.submission_id, []).append(image)

    items = []
    for submission, block, task, student in rows:
        items.append({
            "submission_id": submission.id,
            "block_id": block.id,
            "task_id": task.id,
            "student_id": student.id,
            "task_title": task.title,
            "block_title": block.title or BLOCK_TYPE_LABELS.get(block.block_type, ""),
            "subject": block.subject or task.subject,
            "comment": submission.comment,
            "images": [i.image_s3_url for i in image_map.get(submission.id, [])],
            "overrun": timed_overrun(
                block, get_state(db, block_id=block.id, user_id=student.id)
            ),
            "reviewed": submission.reviewed_at is not None,
            "submitted_at": submission.submitted_at,
        })
    return items
