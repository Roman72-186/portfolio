"""Содержимое элемента дня: блоки конструктора (см. `app/models/task_block.py`).

Работает напрямую по `task_id`, без ORM-relationship к `TrackerTask` — как это
делал `task_quiz.py`, чью роль этот модуль забрал: блоки всегда читаются
свежим запросом, кэша между запросами нет.
"""

from datetime import date as date_type, datetime, timedelta, timezone

from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS, TARIFFS
from app.models.task_block import (
    BLOCK_COMPARE,
    BLOCK_LINK,
    COMPLETABLE_BLOCK_TYPES,
    BLOCK_MEDIA,
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
    IMAGE_BLOCK_TYPES,
    MAX_BLOCK_IMAGES,
    MEDIA_KINDS,
    POLL_BLOCK_TYPES,
    VIDEO_BLOCK_TYPES,
    QUESTION_TEXT,
    QUESTION_TYPES,
    TaskBlock,
    TaskBlockAnswer,
    TaskBlockAnswerOption,
    TaskBlockCompareStep,
    TaskBlockImage,
    TaskBlockOption,
    TaskBlockRequiredTariff,
    TaskBlockResponse,
    TaskBlockState,
    TaskBlockSubmission,
    TaskBlockSubmissionImage,
    TaskBlockTariff,
    TaskBlockTariffDeadline,
    DEADLINE_BLOCKS_COMPLETION,
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


def is_block_open_for_tariff(
    block_tariffs: set[str] | None, user_tariff: str | None
) -> bool:
    """Открыт ли блок этому тарифу. Пустой список тарифов — открыт всем.

    **Единственное место, где записано это правило.** До 28.09.2026 та же
    строчка условия жила в трёх местах (`is_block_accessible` дважды — про сам
    блок и про предыдущий, — и нигде больше, из-за чего экраны блок показывали),
    а ученик чужого тарифа видел в ленте название чужого урока.
    """
    if not block_tariffs:
        return True
    return user_tariff in block_tariffs


def visible_blocks_for_student(
    db: DBSession, blocks: list[TaskBlock], *, user_tariff: str | None
) -> list[TaskBlock]:
    """Блоки, которые ученик вообще вправе видеть.

    Блок, закрытый чужим тарифом, убирается совсем — его для этого ученика не
    существует (владелец 06.09.2026: «не серым „недоступно на вашем тарифе“, а
    не показывать вообще»). Зовётся везде, где блоки уходят ученику: лента
    цикла (`services/cycle_feed.py`), содержимое задания и приём ответов
    (`api/cabinet_tracker.py`), статусы ленты (`feed_state` ниже).

    Своей проверки тарифа ни один из этих слоёв не держит: разъехавшиеся копии
    этого условия и дали прод-расхождение 28.09.2026 — лента показывала блок
    чужого тарифа с подписью «Откроется, когда будет сделано предыдущее», а
    открыться он не мог никогда.
    """
    if not blocks:
        return []
    tariffs_by_block = get_tariffs(db, [block.id for block in blocks])
    return [
        block
        for block in blocks
        if is_block_open_for_tariff(tariffs_by_block.get(block.id), user_tariff)
    ]


def completion_blocker(
    db: DBSession, *, task_id: int, user_id: int, user_tariff: str | None
) -> str | None:
    """Почему ученик ещё не может закрыть задание кнопкой; `None` — может.

    Гейт «нельзя закрыть, пока не отвечены вопросы» (владелец 31.08.2026).
    **Одно правило на два места:** кнопка `toggle` (`api/cabinet_tracker.py`)
    отказывает с этим текстом, а лента (`api/cabinet_learning.py`) рисует по нему
    кнопку выключенной с той же подписью. До 30.09.2026 условие жило только в
    роуте, кнопка была активной, а отказ 409 ученик читал как «Не получилось.
    Попробовать ещё раз» (аудит АОП ученика, находка 2).

    Считаются только **видимые сейчас** вопросы. Скрытые до сдачи в проверку не
    входят — иначе тупик: вопрос не виден, ответить нельзя, задание не закрыть,
    и вся неделя встаёт за ним. Блок чужого тарифа тоже не входит: ученик его
    не видит вовсе (`visible_blocks_for_student`), тот же тупик.
    """
    pending = [
        block for block in question_blocks(
            visible_blocks_for_student(db, get_blocks(db, task_id), user_tariff=user_tariff)
        )
        if not block.hidden_until_done
    ]
    if not pending:
        return None
    # Диагностика проверяется отдельно от остальных вопросов задания
    # (владелец 24.09.2026: она может лежать в одном задании с обычными
    # блоками) — по признаку блока, а не по `task.kind` целиком.
    if any(block.is_diagnostic for block in pending):
        from app.services.archi_profile import result_for_answers
        if result_for_answers(db, task_id, user_id) is None:
            return "Сначала ответь на вопросы диагностики"
    if any(not block.is_diagnostic for block in pending) and (
        get_response(db, task_id=task_id, user_id=user_id) is None
    ):
        return "Сначала ответь на вопросы задания"
    return None


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


def _moment(value) -> datetime | None:
    """Момент из того, что прислали: строка `datetime-local`, `date` или
    `datetime`.

    Форма шлёт строку, но зовут `sync_blocks` и изнутри Python — например
    диагностика, которая размножает настройки одной строки конструктора на
    все свои блоки-вопросы (`_diagnostic_availability`). Раньше строку и
    объект разбирали в разных ветках по типу; после 27.09.2026, когда
    открытие стало нести время, ветка осталась одна, и терпимость к типу
    переехала сюда.

    `date` без времени — полночь по Москве: ровно то, что до 27.09.2026
    делал `msk_midnight`, так что старые вызовы ведут себя как прежде.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date_type):
        return msk_midnight(value).astimezone(timezone.utc)
    return parse_msk_local(value)


def get_submit_deadlines(
    db: DBSession, block_ids: list[int]
) -> dict[int, dict[str, datetime | None]]:
    """Сроки приёма работ по тарифам: блок → {тариф: момент или None}.

    Один запрос на всю ленту, как `get_tariffs`: экран задания читает десятки
    блоков, и поход в базу на каждый превратился бы в N запросов.

    `None` у тарифа — не «срока нет вообще», а «у этого тарифа приём
    бессрочный» (см. докстринг `TaskBlockTariffDeadline`). Отличать приходится
    по наличию ключа, а не по значению, поэтому словарь, а не набор.
    """
    if not block_ids:
        return {}
    rows = (
        db.query(TaskBlockTariffDeadline)
        .filter(TaskBlockTariffDeadline.block_id.in_(block_ids))
        .all()
    )
    grouped: dict[int, dict[str, datetime | None]] = {}
    for row in rows:
        grouped.setdefault(row.block_id, {})[row.tariff] = row.submit_until
    return grouped


def get_task_submit_deadlines(
    db: DBSession, task_ids: list[int]
) -> dict[int, dict[str, datetime | None]]:
    """Сроки по тарифам на уровне задания: задание → {тариф: момент или None}.

    Близнец `get_submit_deadlines`, только на этаж выше. Читаются они всегда
    вместе — блок главнее, задание запасное, — поэтому и форма ответа одна.
    """
    if not task_ids:
        return {}
    from app.models.tracker import TrackerTaskTariffDeadline

    rows = (
        db.query(TrackerTaskTariffDeadline)
        .filter(TrackerTaskTariffDeadline.task_id.in_(task_ids))
        .all()
    )
    grouped: dict[int, dict[str, datetime | None]] = {}
    for row in rows:
        grouped.setdefault(row.task_id, {})[row.tariff] = row.submit_until
    return grouped


def sync_task_submit_deadlines(
    db: DBSession, task, deadlines: list[dict] | None
) -> None:
    """Пересборка сроков по тарифам у задания — копия `sync_submit_deadlines`
    этажом выше, с теми же правилами: снести и собрать заново, неизвестный
    тариф молча отбросить, пустое время сохранить как «бессрочно»."""
    from app.models.tracker import TrackerTaskTariffDeadline

    db.query(TrackerTaskTariffDeadline).filter(
        TrackerTaskTariffDeadline.task_id == task.id
    ).delete(synchronize_session=False)
    seen: set[str] = set()
    for item in deadlines or []:
        tariff = (item.get("tariff") or "").strip().upper()
        if tariff not in TARIFFS or tariff in seen:
            continue
        seen.add(tariff)
        db.add(TrackerTaskTariffDeadline(
            task_id=task.id,
            tariff=tariff,
            submit_until=_moment(item.get("submit_until")),
        ))


def effective_submit_until(
    block: TaskBlock,
    overrides: dict[str, datetime | None] | None,
    user_tariff: str | None,
) -> datetime | None:
    """Срок **самого блока** для этого ученика, без оглядки на задание.

    У тарифа есть своя строка — она главнее общего срока блока, в том числе
    когда в ней пусто («сдача бессрочная»). Нет строки — работает
    `block.submit_until`.

    Обычно звать нужно не её, а `submit_deadline_for`: та добавляет запасной
    срок задания. Эта осталась отдельно, потому что «задал ли блок свой срок»
    — самостоятельный вопрос, и ответ на него нужен, чтобы понять, падать ли
    на уровень задания.
    """
    tariff = (user_tariff or "").strip().upper()
    if overrides and tariff in overrides:
        return overrides[tariff]
    return block.submit_until


def _has_own_deadline(
    block: TaskBlock, overrides: dict[str, datetime | None] | None, user_tariff: str | None
) -> bool:
    """Блок задал свой срок этому ученику — включая «бессрочно».

    Пустая строка тарифа это тоже ответ («у этого тарифа сдача бессрочная»),
    и падать с неё на срок задания нельзя: преподаватель сказал «здесь без
    срока», а задание сказало бы обратное.
    """
    tariff = (user_tariff or "").strip().upper()
    if overrides and tariff in overrides:
        return True
    return block.submit_until is not None


def submit_deadline_for(
    block: TaskBlock | None,
    task,
    *,
    user_tariff: str | None,
    block_overrides: dict[str, datetime | None] | None = None,
    task_overrides: dict[str, datetime | None] | None = None,
) -> datetime | None:
    """До какого момента **этот** ученик может закрыть **этот** блок.

    Единственное место, где сходятся все четыре источника срока, и порядок
    у них такой (владелец 27.09.2026):

    1. строка тарифа у блока — самая частная настройка, главнее всего;
    2. общий срок блока;
    3. строка тарифа у задания;
    4. общий срок задания.

    Блок главнее задания целиком, а не по полю: если блок сказал про себя
    хоть что-то — включая «бессрочно», — срок задания к нему не применяется.
    Иначе «здесь без срока» у блока молча перебивалось бы общим сроком, и
    преподаватель не смог бы сделать ни одного исключения.

    Второй копии этого правила быть не должно: сроки читают и лента, и роуты
    сдачи, и статистика — они обязаны отвечать одинаково, иначе ученик увидит
    «до 9:30», а сервер примет работу в 11:00 (или наоборот).

    `block=None` — срок задания без блока: домашка (владелец 28.09.2026: «домашка
    также должна работать по всем правилам»). Тогда остаются пункты 3 и 4.
    """
    if block is not None and _has_own_deadline(block, block_overrides, user_tariff):
        return effective_submit_until(block, block_overrides, user_tariff)
    if task is None:
        return None
    tariff = (user_tariff or "").strip().upper()
    if task_overrides and tariff in task_overrides:
        return task_overrides[tariff]
    return getattr(task, "submit_until", None)


def submit_deadline_is_set(
    block: TaskBlock | None,
    task,
    *,
    user_tariff: str | None,
    block_overrides: dict[str, datetime | None] | None = None,
    task_overrides: dict[str, datetime | None] | None = None,
) -> bool:
    """Сказал ли кто-то из четырёх источников `submit_deadline_for` про срок
    хоть что-то — включая явное «бессрочно» строкой тарифа.

    Нужна там, где у пустого срока есть запасной вариант (статистика берёт
    конец цикла, владелец 28.09.2026): `submit_deadline_for` отдаёт `None` и на
    «ничего не настроено», и на «здесь без срока», а перебивать второе нельзя.
    """
    if block is not None and _has_own_deadline(block, block_overrides, user_tariff):
        return True
    if task is None:
        return False
    tariff = (user_tariff or "").strip().upper()
    if task_overrides and tariff in task_overrides:
        return True
    return getattr(task, "submit_until", None) is not None


def sync_submit_deadlines(
    db: DBSession, block: TaskBlock, deadlines: list[dict] | None
) -> None:
    """Полная пересборка сроков по тарифам — копия `_sync_tariffs`, та же
    причина сноса-и-пересборки и молчаливого отбрасывания неизвестного тарифа.

    Публичная, в отличие от соседок: её зовёт не только `sync_blocks`, но и
    быстрая правка срока из списка заданий (`api/cabinet_program.py`).

    Строка заводится по выбранному тарифу, даже когда время пустое: пустое и
    есть «у этого тарифа приём бессрочный». Строка без тарифа отбрасывается —
    общий срок блока живёт в своей колонке, не здесь.
    """
    db.query(TaskBlockTariffDeadline).filter(
        TaskBlockTariffDeadline.block_id == block.id
    ).delete(synchronize_session=False)
    seen: set[str] = set()
    for item in deadlines or []:
        tariff = (item.get("tariff") or "").strip().upper()
        if tariff not in TARIFFS or tariff in seen:
            continue
        seen.add(tariff)
        db.add(TaskBlockTariffDeadline(
            block_id=block.id,
            tariff=tariff,
            submit_until=_moment(item.get("submit_until")),
        ))


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

    У блока сравнения ответы учеников на картинки всё-таки ссылаются — поэтому
    там ответ хранится URL-ом, а не id (см. `BLOCK_COMPARE`), и пересборка
    его не рвёт.
    """
    db.query(TaskBlockImage).filter(
        TaskBlockImage.block_id == block.id
    ).delete(synchronize_session=False)
    is_compare = block.block_type == BLOCK_COMPARE
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
                # Отметка «мой выбор» — только у сравнения: у галереи фото
                # она ничего не значит, и блок могли переключить с одного
                # типа на другой.
                is_pick=bool(raw.get("is_pick")) if is_compare else False,
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


def poll_submission_error(
    blocks: list[TaskBlock], *, answered_ids: set[int], answers: dict[int, dict]
) -> str | None:
    """Отказ, если первая отправка опроса неполная; `None` — можно сохранять.

    Опрос (владелец 30.09.2026) проходится мастером и уходит одним запросом.
    Раз в запросе есть вопрос опроса, у которого остались неотвеченные, в нём
    должны быть все неотвеченные вопросы этого опроса и ответ на каждый.
    Иначе половина опроса закрылась бы, половина висела, и в ленте опрос
    выглядел бы пройденным наполовину. После первой отправки действуют
    обычные правила вопроса и шкалы: выбор — одна попытка, текст и оценки
    можно менять до проверки. Тот же приём, что у диагностики
    (`submit_cabinet_tracker_task_blocks`).

    `answers` — `{block_id: {"text", "option_ids", "option_texts"}}`.
    """
    groups: dict[str, list[TaskBlock]] = {}
    for block in blocks:
        if block.poll_key and block.block_type in POLL_BLOCK_TYPES:
            groups.setdefault(block.poll_key, []).append(block)
    for group in groups.values():
        pending = [block for block in group if block.id not in answered_ids]
        if not pending or not any(block.id in answers for block in group):
            continue
        for block in pending:
            answer = answers.get(block.id)
            if answer is None:
                return "Ответь на все вопросы опроса"
            if block.block_type == BLOCK_QUESTION and block.question_type == QUESTION_TEXT:
                filled = bool((answer.get("text") or "").strip())
            else:
                filled = bool(answer.get("option_ids"))
            if not filled:
                return "Ответь на все вопросы опроса"
    return None


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
    if block_type in (BLOCK_PHOTO, BLOCK_COMPARE):
        # У сравнения описание необязательно, содержимое — сами работы.
        # Без этой ветки блок с работами, но без описания, падал бы в
        # проверку `body` внизу и молча пропадал. Меньше двух работ и отметку
        # «мой выбор» проверяет схема конструктора, до сервиса такое не доходит.
        return not [
            image for image in (item.get("images") or [])
            if (image.get("url") or "").strip()
        ]
    if block_type == BLOCK_LINK:
        return not (item.get("url") or "").strip()
    if block_type == BLOCK_MEDIA:
        # Голосовое / кружок без записи — пустая заготовка: преподаватель
        # нажал «плюс» и не записал ничего.
        return (
            not (item.get("media_url") or "").strip()
            or item.get("media_kind") not in MEDIA_KINDS
        )
    if block_type == BLOCK_TIMED:
        # Кнопка «Начать» самодостаточна, как и «Загрузить портфолио».
        return False
    if block_type in (BLOCK_SCALE, BLOCK_RULES):
        return not [
            option for option in (item.get("options") or [])
            if (option.get("text") or "").strip()
        ]
    if block_type in (BLOCK_PORTFOLIO, BLOCK_UPLOAD, BLOCK_PHOTO_UPLOAD):
        # Кнопки «Загрузить портфолио» и «Домашнее задание» самодостаточны:
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
        row.video_id = item.get("video_id") if block_type in VIDEO_BLOCK_TYPES else None
        row.url = _clean(item.get("url"), 500) if block_type == BLOCK_LINK else None
        is_media = block_type == BLOCK_MEDIA
        row.media_kind = item.get("media_kind") if is_media else None
        row.media_s3_url = _clean(item.get("media_url"), 500) if is_media else None
        row.media_s3_path = _clean(item.get("media_path"), 500) if is_media else None
        # У шкалы — только внутри опроса: галочка «показать после закрытия
        # задания» одна на весь опрос, и шкала без неё вылезла бы раньше
        # остальных его вопросов.
        row.hidden_until_done = bool(
            item.get("hidden_until_done")
            if block_type == BLOCK_QUESTION
            or (block_type == BLOCK_SCALE and item.get("poll_key"))
            else False
        )
        # Блок-вопрос диагностики АРХИ-ПРОФИЛЯ (владелец 24.09.2026) —
        # проставляется `archi_profile.blocks_from_config`/`preset_blocks`,
        # чужим блокам-вопросам, добавленным тем же конструктором, не
        # передаётся и остаётся False.
        row.is_diagnostic = bool(
            item.get("is_diagnostic") if block_type == BLOCK_QUESTION else False
        )
        # Опрос (владелец 30.09.2026): ключ и описание разворачивает из одной
        # строки конструктора `api/cabinet_program.py::_expand_poll_entry`.
        # Бывают только у вопроса и шкалы — блок могли переключить на другой
        # тип, и чужой ключ склеил бы его с опросом.
        in_poll = block_type in POLL_BLOCK_TYPES
        row.poll_key = _clean(item.get("poll_key"), 32) if in_poll else None
        row.poll_intro = (
            (item.get("poll_intro") or "").strip() or None
        ) if in_poll and row.poll_key else None
        row.is_required = bool(item.get("is_required"))
        row.is_required_for_intake = bool(
            item.get("is_required_for_intake", True)
            if block_type == BLOCK_PORTFOLIO else False
        )
        subject = _clean(item.get("subject"), 50)
        row.subject = subject if subject in MOCK_SUBJECTS else None
        row.bypass_sequence = bool(item.get("bypass_sequence"))
        # Лимит — только у работы на время; смена типа блока его убирает.
        limit = item.get("time_limit_minutes")
        row.time_limit_minutes = (
            int(limit) if block_type == BLOCK_TIMED and limit else None
        )
        window_hours = item.get("portfolio_window_hours")
        row.portfolio_window_hours = (
            int(window_hours)
            if block_type == BLOCK_PORTFOLIO and window_hours else None
        )
        # Открытие несёт время суток с 27.09.2026 (владелец: «нужны не только
        # даты, а время»). До этого поле было `type="date"` и сервер считал
        # `msk_midnight`, то есть открыть задание к 10:00 было нечем. Формат
        # теперь тот же, что у `closes_at`, — строка `datetime-local`; у
        # блоков, заведённых раньше, в базе лежит полночь, и она просто
        # показывается как «00:00».
        row.opens_at = _moment(item.get("opens_at"))
        row.closes_at = _moment(item.get("closes_at"))
        # Закрытие раньше открытия — куратор перепутал поля; отбрасываем
        # молча, как и везде в этой функции с некорректным вводом, а не
        # роняем сохранение всего блока (см. неизвестный тариф/предмет выше).
        if (
            row.closes_at is not None
            and row.opens_at is not None
            and row.closes_at <= row.opens_at
        ):
            row.closes_at = None
        # Срок — у любого типа блока (владелец 27.09.2026, второй заход:
        # «добавить в доступность блока и для всех заданий»). Что он делает,
        # зависит от типа: у сдачи и ответов запирает, у видео, фото, текста,
        # ссылки и голосового только показывается ученику и попадает в
        # статистику «до срока / после срока» (см. DEADLINE_BLOCKS_COMPLETION).
        row.submit_until = _moment(item.get("submit_until"))
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
        # Картинки — у галереи, у комбинированного «Фото + сдача работы» и у
        # кнопки «Загрузить портфолио» (примеры и скриншоты к инструкции);
        # блок могли переключить с фото на текст.
        _sync_images(
            db, row,
            item.get("images") if row.block_type in IMAGE_BLOCK_TYPES else [],
        )
        _sync_tariffs(db, row, item.get("tariffs"))
        _sync_required_tariffs(db, row, item.get("required_tariffs"))
        sync_submit_deadlines(db, row, item.get("submit_deadlines"))
    removed = [row for block_id, row in existing.items() if block_id not in matched_ids]
    _refuse_dropping_submitted(db, removed)
    for row in removed:
        _drop_block(db, row)
    db.flush()
    return [row for row, _ in paired]


class BlockHasSubmissionsError(Exception):
    """Из формы убран блок, в котором ученики уже сдали работы.

    Не `ValueError`: роуты конструктора ловят `ValueError` как ошибку ввода
    (422), а это отказ по состоянию данных — 409.
    """


def _refuse_dropping_submitted(db: DBSession, blocks: list[TaskBlock]) -> None:
    """Не дать удалить блок со сдачами (код-ревью 28.09.2026, P1).

    `_drop_block` сдачи сам не чистит, а на проде их вместе с файлами и
    перепиской по проверке молча уносил каскад Postgres
    (`task_block_submissions.block_id ON DELETE CASCADE`). Явная проверка, а
    не расчёт на отказ внешнего ключа: SQLite в тестах ключи не исполняет —
    образец тот же, что у снятия билета с пробника (`cabinet_program.py`).
    """
    if not blocks:
        return
    busy_ids = {
        row.block_id
        for row in db.query(TaskBlockSubmission.block_id)
        .filter(TaskBlockSubmission.block_id.in_([b.id for b in blocks]))
        .distinct()
        .all()
    }
    if not busy_ids:
        return
    names = [
        f"«{b.title}»" if b.title else BLOCK_TYPE_LABELS.get(b.block_type, b.block_type)
        for b in blocks if b.id in busy_ids
    ]
    raise BlockHasSubmissionsError(
        "Нельзя убрать блок " + ", ".join(names)
        + ": ученики уже сдали в нём работы. Верните блок в задание и сохраните снова."
    )


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


# --- сравнение работ (Лиза 27.09.2026, см. BLOCK_COMPARE) --------------------


class CompareChoiceError(ValueError):
    """Выбор в блоке сравнения не принят. `already` — ответ уже есть
    (одна попытка), иначе — работы с таким адресом в блоке нет."""

    def __init__(self, message: str, *, already: bool = False):
        super().__init__(message)
        self.already = already


def compare_pick_url(images: list[TaskBlockImage]) -> str | None:
    """Работа, которую отметил преподаватель."""
    return next((image.image_s3_url for image in images if image.is_pick), None)


def compare_work_label(images: list[TaskBlockImage], url: str | None) -> str:
    """«Работа №k» по месту в галерее — так их видит и ученик, и преподаватель.

    Адреса в галерее может уже не быть: преподаватель убрал работу после
    ответа ученика. Тогда подпись об этом и говорит, а не падает.
    """
    for position, image in enumerate(images, start=1):
        if image.image_s3_url == url:
            return f"Работа №{position}"
    return "Работа убрана из задания"


def get_compare_answer(
    db: DBSession, *, block_id: int, user_id: int, task_id: int
) -> TaskBlockAnswer | None:
    response = get_response(db, task_id=task_id, user_id=user_id)
    if response is None:
        return None
    return (
        db.query(TaskBlockAnswer)
        .filter(
            TaskBlockAnswer.response_id == response.id,
            TaskBlockAnswer.block_id == block_id,
        )
        .one_or_none()
    )


def save_compare_choice(
    db: DBSession, *, block: TaskBlock, user_id: int, image_url: str
) -> bool:
    """Сохранить победившую работу ученика и закрыть блок. Возвращает, совпал
    ли выбор с преподавательским.

    Одна попытка, как у вопроса с вариантами: иначе, увидев «не совпало»,
    можно было бы переотправлять до совпадения. Адрес принимается только из
    галереи этого блока — чужой ссылкой ответ не подделать.

    Ответ пишется в ту же строку заполнения `TaskBlockResponse`, что и ответы
    на вопросы задания, поэтому экран проверки видит его без своей ветки.
    """
    url = (image_url or "").strip()
    images = get_images(db, [block.id]).get(block.id, [])
    if url not in {image.image_s3_url for image in images}:
        raise CompareChoiceError("Такой работы в задании нет")
    if get_compare_answer(
        db, block_id=block.id, user_id=user_id, task_id=block.task_id
    ) is not None:
        raise CompareChoiceError("Выбор уже сохранён", already=True)
    response = get_response(db, task_id=block.task_id, user_id=user_id)
    if response is None:
        response = TaskBlockResponse(task_id=block.task_id, user_id=user_id)
        db.add(response)
        db.flush()
    else:
        # Строка заполнения могла появиться раньше — ученик уже отвечал на
        # вопросы этого задания. Новая строка ответа её не трогает, а экран
        # проверки сортирует и режет по неделе именно по `updated_at`.
        response.updated_at = datetime.now(timezone.utc)
    db.add(TaskBlockAnswer(response_id=response.id, block_id=block.id, text=url))
    close_block_for_user(db, block=block, user_id=user_id, source="compare")
    return url == compare_pick_url(images)


def _compare_steps_query(db: DBSession, *, block_id: int, user_id: int):
    return db.query(TaskBlockCompareStep).filter(
        TaskBlockCompareStep.block_id == block_id,
        TaskBlockCompareStep.user_id == user_id,
    )


def compare_progress(
    db: DBSession, *, block_id: int, user_id: int, images: list[TaskBlockImage]
) -> dict | None:
    """Пара, на которой ученик остановился (владелец 28.09.2026: после
    перезагрузки — туда же, а не с начала).

    Работы идут в порядке галереи: пара k — победитель прошлых пар против
    работы №k+1. Если преподаватель переставил или убрал работы посреди
    турнира, сохранённые пары описывают уже другую галерею — тогда `stale`,
    и турнир идёт с первой пары. Сами шаги здесь не стираются: лента — чтение,
    стирает их следующий выбор (`save_compare_step`).
    """
    works = [image.image_s3_url for image in images]
    if len(works) < 2:
        return None
    total = len(works) - 1
    steps = _compare_steps_query(db, block_id=block_id, user_id=user_id).order_by(
        TaskBlockCompareStep.step
    ).all()
    stale = bool(steps) and (
        len(steps) >= total
        or any(
            s.step != position
            or s.right_url != works[position]
            or s.winner_url not in (s.left_url, s.right_url)
            for position, s in enumerate(steps, start=1)
        )
        or steps[-1].winner_url not in works
    )
    if stale or not steps:
        champion, step = works[0], 1
    else:
        champion, step = steps[-1].winner_url, len(steps) + 1
    return {
        "champion_url": champion,
        "challenger_url": works[step],
        "step": step,
        "total": total,
        "stale": stale,
    }


def save_compare_step(
    db: DBSession, *, block: TaskBlock, user_id: int, image_url: str,
    step: int | None = None,
) -> dict:
    """Выбор ученика в текущей паре. Окончательный: пара, которую сервер
    считает текущей, — единственная, в которой можно выбирать, так что ни
    переиграть прошлую, ни перескочить вперёд нельзя.

    На последней паре сразу пишет итоговый ответ (`save_compare_choice`) и
    возвращает вердикт — отдельной кнопки «Отправить» нет (владелец 28.09.2026).
    """
    url = (image_url or "").strip()
    if get_compare_answer(
        db, block_id=block.id, user_id=user_id, task_id=block.task_id
    ) is not None:
        raise CompareChoiceError("Выбор уже сохранён", already=True)
    images = get_images(db, [block.id]).get(block.id, [])
    progress = compare_progress(db, block_id=block.id, user_id=user_id, images=images)
    if progress is None:
        raise CompareChoiceError("Работы для сравнения ещё не загружены")
    # Номер пары от браузера: второе нажатие той же пары (двойной клик, две
    # вкладки) иначе засчиталось бы выбором в следующей.
    if step is not None and step != progress["step"]:
        raise CompareChoiceError("Этот выбор уже сохранён. Обнови страницу", already=True)
    if url not in (progress["champion_url"], progress["challenger_url"]):
        raise CompareChoiceError("Этой работы нет в текущей паре. Обнови страницу")
    if progress["stale"]:
        _compare_steps_query(db, block_id=block.id, user_id=user_id).delete(
            synchronize_session=False
        )
    db.add(TaskBlockCompareStep(
        block_id=block.id,
        user_id=user_id,
        step=progress["step"],
        left_url=progress["champion_url"],
        right_url=progress["challenger_url"],
        winner_url=url,
    ))
    db.flush()
    if progress["step"] < progress["total"]:
        next_step = progress["step"] + 1
        return {
            "finished": False,
            "champion_url": url,
            "challenger_url": images[next_step].image_s3_url,
            "step": next_step,
            "total": progress["total"],
        }
    matched = save_compare_choice(db, block=block, user_id=user_id, image_url=url)
    return {
        "finished": True,
        "chosen_url": url,
        "matched": matched,
        "pick_url": compare_pick_url(images),
    }


def get_compare_steps(
    db: DBSession, pairs: list[tuple[int, int]]
) -> dict[tuple[int, int], list[TaskBlockCompareStep]]:
    """Ход выбора пачкой: (block_id, user_id) → пары по порядку."""
    wanted = set(pairs)
    if not wanted:
        return {}
    rows = (
        db.query(TaskBlockCompareStep)
        .filter(
            TaskBlockCompareStep.block_id.in_({b for b, _u in wanted}),
            TaskBlockCompareStep.user_id.in_({u for _b, u in wanted}),
        )
        .order_by(TaskBlockCompareStep.step)
        .all()
    )
    grouped: dict[tuple[int, int], list[TaskBlockCompareStep]] = {}
    for row in rows:
        key = (row.block_id, row.user_id)
        if key in wanted:
            grouped.setdefault(key, []).append(row)
    return grouped


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


def portfolio_window_deadline(
    block: TaskBlock, state: TaskBlockState | None
) -> datetime | None:
    """Персональный дедлайн блока или None, пока отсчёт не начался."""
    if (
        block.block_type != BLOCK_PORTFOLIO
        or not block.portfolio_window_hours
        or state is None
        or state.started_at is None
    ):
        return None
    started_at = state.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return started_at + timedelta(hours=block.portfolio_window_hours)


def portfolio_window_expired(
    block: TaskBlock, state: TaskBlockState | None, *, now=None
) -> bool:
    deadline = portfolio_window_deadline(block, state)
    return deadline is not None and deadline <= (now or _now())


def start_portfolio_window(
    db: DBSession, *, block: TaskBlock, user_id: int, now=None
) -> TaskBlockState:
    """Запомнить первый момент доступности, не продлевая окно при возврате."""
    state = get_state(db, block_id=block.id, user_id=user_id)
    if state is None:
        state = TaskBlockState(block_id=block.id, user_id=user_id, status=STATUS_OPEN)
        db.add(state)
        db.flush()
    if state.started_at is None:
        state.started_at = now or _now()
        db.flush()
    return state


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
    maybe_close_task_by_blocks(db, block.task_id, user_id)
    return state


# Задания, которые закрываются своими путями, — у них и кнопки
# «Завершить задание» нет (`partials/task_action.html`, `autocloses`).
_SELF_CLOSING_TASK_KINDS = ("homework", "mock_exam")


def autoclose_steps(db: DBSession, task_id: int, user_tariff: str | None) -> list[TaskBlock]:
    """Шаги, по которым задание закрывается само (`maybe_close_task_by_blocks`).

    Блоки, видимые ученику по тарифу, кроме скрытых до сдачи
    (`hidden_until_done`) и тех, что отметить нечем (текст, ссылка —
    `COMPLETABLE_BLOCK_TYPES`). Одно определение на автозакрытие и на кнопку
    «Завершить задание» в ленте (`completion_button_needed`) — разойдись они,
    кнопка пропала бы там, где задание само не закроется.
    """
    blocks = visible_blocks_for_student(db, get_blocks(db, task_id), user_tariff=user_tariff)
    return [
        block for block in blocks
        if block.block_type in COMPLETABLE_BLOCK_TYPES and not block.hidden_until_done
    ]


def completion_button_needed(
    db: DBSession, *, task_id: int, user_id: int, user_tariff: str | None
) -> bool:
    """Нужна ли ученику кнопка «Завершить задание» у незакрытого задания из блоков.

    Владелец 01.10.2026 (аудит АОП, находка 8): «убираем лишнюю». Лишняя она там,
    где задание закроется само, как только ученик сделает обязательное, — то есть
    все шаги автозакрытия обязательные. Кнопка остаётся:

    - у задания без шагов (только текст и ссылка) — закрыть его больше нечем;
    - если хоть один шаг необязательный: автозакрытие ждёт и его, и без кнопки
      необязательный ролик стал бы обязательным;
    - если все шаги уже отмечены, а задание открыто (отметки до автозакрытия
      30.09.2026, сбой) — страховка от тупика.
    """
    steps = autoclose_steps(db, task_id, user_tariff)
    if not steps or not all(block.is_required for block in steps):
        return True
    states = get_states(db, block_ids=[block.id for block in steps], user_id=user_id)
    return all(
        states.get(block.id) is not None and states[block.id].status == STATUS_DONE
        for block in steps
    )


def maybe_close_task_by_blocks(db: DBSession, task_id: int, user_id: int) -> bool:
    """Закрыть задание, когда ученик сделал все свои шаги (владелец 30.09.2026).

    До этого задание из блоков закрывалось только кнопкой «Завершить задание».
    Её не нажимали, а у должника прошлого цикла её не было вовсе (в архиве
    кнопка скрыта, а кружки работают) — 30.09 цикл 3 «не сдали» 27 человек,
    из них 17 без кнопки. Теперь последний отмеченный шаг закрывает и задание.

    Шаги — `autoclose_steps`. Необязательные блоки входят: закрыть задание
    раньше, пропустив их, можно кнопкой (поэтому у такого задания она остаётся,
    `completion_button_needed`), а сама система не закрывает его, пока что-то
    не сделано. Задание без выполнимых шагов само не закрывается.

    Зовётся из `close_block_for_user` — через неё идут все источники отметки,
    своих копий в роутах нет. Возвращает, закрыли ли задание сейчас.
    """
    from app.models.tracker import TrackerTask, TrackerTaskState
    from app.models.user import User
    from app.services.tracker import close_task_for_user

    task = db.get(TrackerTask, task_id)
    if task is None or task.kind in _SELF_CLOSING_TASK_KINDS:
        return False
    current = (
        db.query(TrackerTaskState.status)
        .filter(TrackerTaskState.task_id == task_id, TrackerTaskState.user_id == user_id)
        .scalar()
    )
    if current == STATUS_DONE:
        return False
    user = db.get(User, user_id)
    steps = autoclose_steps(db, task_id, user.tariff if user else None)
    if not steps:
        return False
    states = get_states(db, block_ids=[block.id for block in steps], user_id=user_id)
    if not all(
        states.get(block.id) is not None and states[block.id].status == STATUS_DONE
        for block in steps
    ):
        return False
    close_task_for_user(db, task, user_id, source="blocks_done")
    return True


def close_tasks_with_all_blocks_done(db: DBSession) -> int:
    """Бэкфилл `maybe_close_task_by_blocks` по уже отмеченным шагам: задания,
    где ученик сделал всё до появления автозакрытия (30.09.2026). Идемпотентно,
    не коммитит. Возвращает, сколько заданий закрыто."""
    pairs = (
        db.query(TaskBlockState.user_id, TaskBlock.task_id)
        .join(TaskBlock, TaskBlock.id == TaskBlockState.block_id)
        .filter(TaskBlockState.status == STATUS_DONE)
        .distinct()
        .all()
    )
    return sum(
        1 for user_id, task_id in pairs
        if maybe_close_task_by_blocks(db, task_id, user_id)
    )


def is_block_accessible(
    *,
    block_index: int,
    blocks: list[TaskBlock],
    states: dict[int, TaskBlockState],
    tariffs_by_block: dict[int, set[str]],
    user_tariff: str | None,
    required_tariffs_by_block: dict[int, set[str]] | None = None,
    required_by_block: dict[int, bool] | None = None,
    submit_deadlines_by_block: dict[int, dict[str, datetime | None]] | None = None,
    tasks_by_id: dict | None = None,
    task_submit_deadlines_by_task: dict[int, dict[str, datetime | None]] | None = None,
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
       остаётся видимым. **Истёкший срок приёма работ** (`submit_until`,
       владелец 27.09.2026) снимает блокировку по той же причине: приём
       закрыт, сдать уже нечем, и требовать выполнения — тупик без выхода.

    **Срок приёма работ доступность самого блока не меняет** — в этом и его
    смысл: после 9:30 ученик по-прежнему видит задание, свою работу, оценку и
    переписку, закрыта только сдача (владелец 27.09.2026). Закрытие сдачи
    считает `services/submission_edit.py::deadline_reason`, здесь срок нужен
    ровно для развязки тупика в пункте 4.

    `target.bypass_sequence` пропускает только пункт 4, не 1, 2 и 3
    (владелец 06.09.2026). Раньше это было жёстко зашито на `BLOCK_LINK`
    («ссылка на занятие видна сразу, не дожидаясь предыдущих блоков») — но
    тот же созвон 03.09 требует обратного для тарифа «Уверенный максимум»:
    та же ссылка должна ждать сдачи домашки. Явный флаг снимает конфликт без
    ветвления по тарифу в коде: куратор делает две версии блока-ссылки —
    одну с `bypass_sequence=True` без тарифа (видна всем сразу), другую с
    `bypass_sequence=False` и тарифом `["МАКСИМУМ"]` (обычная очередь).

    Видимость: блок, недоступный по тарифу, в готовой ленте не показывается
    вообще, а не серым «недоступно на вашем тарифе» (владелец 06.09.2026).
    **С 28.09.2026 это выполнено, и не здесь:** скрытие делает
    `visible_blocks_for_student` выше — его зовут все слои, где блоки уходят
    ученику, и до этой функции чужой блок уже не доходит. Сама она по-прежнему
    возвращает чистый True/False и тариф проверяет ради `feed_state` и прямых
    вызовов. Прецедент 28.09.2026: решение 06.09 до шаблона ленты так и не
    довели, и ученик тарифа «Я С ВАМИ» видел в ленте карточку «Уверенный
    максимум» с подписью «Откроется, когда будет сделано предыдущее» — открыться
    она не могла никогда.
    """
    moment = now or _now()
    target = blocks[block_index]
    opens_at = target.opens_at
    if opens_at is not None:
        if opens_at.tzinfo is None:
            opens_at = opens_at.replace(tzinfo=timezone.utc)
        if opens_at > moment:
            return False
    target_state = states.get(target.id)
    # У нового портфолио абсолютный closes_at заменён персональным окном:
    # каждому ученику даётся одинаковое число часов с его момента старта.
    if target.block_type == BLOCK_PORTFOLIO and target.portfolio_window_hours:
        if portfolio_window_expired(target, target_state, now=moment):
            return False
    else:
        closes_at = target.closes_at
        if closes_at is not None:
            if closes_at.tzinfo is None:
                closes_at = closes_at.replace(tzinfo=timezone.utc)
            if closes_at <= moment:
                return False
    if not is_block_open_for_tariff(tariffs_by_block.get(target.id), user_tariff):
        return False
    if target.bypass_sequence:
        return True
    for prior in blocks[:block_index]:
        prior_is_required = (
            required_by_block.get(prior.id, prior.is_required)
            if required_by_block is not None else prior.is_required
        )
        if not prior_is_required:
            continue
        if not is_block_open_for_tariff(tariffs_by_block.get(prior.id), user_tariff):
            continue
        prior_required_tariffs = (
            required_tariffs_by_block.get(prior.id) if required_tariffs_by_block else None
        )
        if prior_required_tariffs and user_tariff not in prior_required_tariffs:
            continue
        state = states.get(prior.id)
        if (
            prior.block_type == BLOCK_PORTFOLIO
            and prior.portfolio_window_hours
            and portfolio_window_expired(prior, state, now=moment)
        ):
            continue
        prior_closes_at = (
            None
            if prior.block_type == BLOCK_PORTFOLIO and prior.portfolio_window_hours
            else prior.closes_at
        )
        if prior_closes_at is not None:
            prior_closes = (
                prior_closes_at if prior_closes_at.tzinfo
                else prior_closes_at.replace(tzinfo=timezone.utc)
            )
            if prior_closes <= moment:
                continue
        # Срок у обязательного блока прошёл, а ученик не закрыл его: если
        # действие отобрал сам срок (сдача, ответ, правила), закрыть блок уже
        # нечем, и без этой развязки лента встала бы навсегда — ровно тот же
        # тупик, что выше снимают тариф и `closes_at` (владелец 27.09.2026).
        #
        # У видео, фото и голосового срок ничего не отбирает: отметить
        # «Выполнено» можно и после него, это просто зачтётся опозданием в
        # статистике. Такой блок очередь держит дальше — иначе срок,
        # поставленный ради отчётности, молча снимал бы обязательность.
        # Задание берётся по самому блоку, а не «то, ради которого позвали»:
        # в ленте цикла блоки идут подряд из разных заданий, и чужой срок
        # задания запер бы или отпустил не тот блок.
        prior_task = (tasks_by_id or {}).get(prior.task_id)
        prior_submit_until = (
            submit_deadline_for(
                prior, prior_task,
                user_tariff=user_tariff,
                block_overrides=(submit_deadlines_by_block or {}).get(prior.id),
                task_overrides=(task_submit_deadlines_by_task or {}).get(prior.task_id),
            )
            if prior.block_type in DEADLINE_BLOCKS_COMPLETION else None
        )
        if prior_submit_until is not None:
            prior_submit = (
                prior_submit_until if prior_submit_until.tzinfo
                else prior_submit_until.replace(tzinfo=timezone.utc)
            )
            if prior_submit <= moment:
                continue
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


def timed_seconds_left(
    block: TaskBlock, state: TaskBlockState | None, *, now: datetime | None = None
) -> int | None:
    """Сколько секунд осталось по таймеру контрольной; меньше нуля — время
    вышло. `None` — не начата или лимита нет.

    Считает сервер, а не браузер: часы на телефоне ученика бывают сбиты на
    минуты, и обратный отсчёт по ним показал бы не то время, по которому
    потом решает `timed_overrun` (владелец 30.09.2026 попросил отсчёт на экране).
    """
    if state is None or state.started_at is None or block.time_limit_minutes is None:
        return None
    started = state.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    spent = ((now or _now()) - started).total_seconds()
    return int(block.time_limit_minutes * 60 - spent)


def completed_after_deadline(
    block: TaskBlock,
    task,
    state: TaskBlockState | None,
    *,
    user_tariff: str | None,
    block_overrides: dict[str, datetime | None] | None = None,
    task_overrides: dict[str, datetime | None] | None = None,
) -> bool:
    """Закрыт ли блок позже срока сдачи — отметка «сдано после срока».

    Срок — `submit_deadline_for`, тот же, что видит ученик и что запирает
    сдачу; момент — `completed_at`, первая сдача, как в статистике
    (`activity_stats.get_deadline_stats`). Запасного срока «конец цикла»
    здесь нет: он только для отчёта, ученику такого срока не показывали.
    """
    if state is None or state.completed_at is None:
        return False
    deadline = submit_deadline_for(
        block, task, user_tariff=user_tariff,
        block_overrides=block_overrides, task_overrides=task_overrides,
    )
    if deadline is None:
        return False
    finished = state.completed_at
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return finished > deadline


def block_status(
    block: TaskBlock, state: TaskBlockState | None, *, accessible: bool
) -> str:
    """`"locked"` | `"current"` | `"done"` — для рендера ленты."""
    if not accessible:
        return "locked"
    if state is not None and state.status == STATUS_DONE:
        return "done"
    return "current"


def is_block_required_for_user(
    block: TaskBlock, *, is_intake_student: bool
) -> bool:
    """Какой флаг обязательности действует для этого ученика.

    Отдельное правило «Пробы» пока есть только у загрузки
    портфолио. Остальные типы смотрят на общий `is_required`.

    **Текст и ссылка обязательными не бывают** (владелец 01.10.2026, аудит АОП
    ученика): отметить их нечем (`COMPLETABLE_BLOCK_TYPES`), и обязательный
    текст запирал бы всё ниже навсегда — «Откроется, когда будет сделано
    предыдущее» без выхода. Флаг в базе остаётся, но не действует; в
    конструкторе галочки у них нет (`blockSettingsHTML`). Через эту функцию
    идут очередь ленты (`cycle_feed.build_cycle_feed`, `feed_state`) и
    подпись «что держит» — своей копии условия у них нет.
    """
    if block.block_type not in COMPLETABLE_BLOCK_TYPES:
        return False
    if is_intake_student and block.block_type == BLOCK_PORTFOLIO:
        return block.is_required_for_intake
    return block.is_required


def poll_inner_block_ids(blocks: list[TaskBlock]) -> set[int]:
    """Блоки опроса, кроме последнего в своём опросе.

    Для очереди ленты обязателен только последний вопрос опроса (владелец
    30.09.2026). Опрос проходится одним мастером и уходит одним запросом:
    считай обязательным каждый вопрос — второй оказался бы заперт до ответа
    на первый, и мастер не собрался бы. Хвост ленты опрос держит так же —
    последний вопрос закрывается вместе с остальными. Тот же приём, что у
    диагностики вида `archi_profile` (`cycle_feed.build_cycle_feed`).
    """
    last_by_key: dict[str, int] = {}
    poll_ids: list[tuple[str, int]] = []
    for block in blocks:
        if block.poll_key and block.block_type in POLL_BLOCK_TYPES:
            last_by_key[block.poll_key] = block.id
            poll_ids.append((block.poll_key, block.id))
    return {block_id for key, block_id in poll_ids if last_by_key[key] != block_id}


def feed_state(
    db: DBSession, *, task_id: int, user_id: int, user_tariff: str | None
) -> list[dict]:
    """Блоки задачи одним запросом + статус для конкретного ученика.

    Возвращает список `{"block": TaskBlock, "status": str, "state":
    TaskBlockState | None}` в порядке `sort_order` — ровно то, что нужно
    шаблону единой ленты для рендера, без похода в базу на каждый блок.
    """
    from app.models.tracker import ITEM_MOCK_EXAM, TrackerTask
    from app.models.user import User

    blocks = visible_blocks_for_student(
        db, get_blocks(db, task_id), user_tariff=user_tariff
    )
    task = db.get(TrackerTask, task_id)
    student = db.get(User, user_id)
    is_intake_student = bool(student and student.access_until is not None)
    task_blocks_progress = bool(
        task and task.is_required and task.kind != ITEM_MOCK_EXAM
    )
    poll_inner = poll_inner_block_ids(blocks)
    required_by_block = {
        block.id: bool(
            task_blocks_progress
            and block.id not in poll_inner
            and is_block_required_for_user(
                block, is_intake_student=is_intake_student
            )
        )
        for block in blocks
    }
    block_ids = [block.id for block in blocks]
    states = get_states(db, block_ids=block_ids, user_id=user_id)
    tariffs_by_block = get_tariffs(db, block_ids)
    submit_deadlines_by_block = get_submit_deadlines(db, block_ids)
    task_submit_deadlines = get_task_submit_deadlines(db, [task_id]).get(task_id)
    now = _now()  # один и тот же момент для всех блоков ленты, не по одному на блок
    result: list[dict] = []
    for index, block in enumerate(blocks):
        accessible = is_block_accessible(
            block_index=index,
            blocks=blocks,
            states=states,
            tariffs_by_block=tariffs_by_block,
            user_tariff=user_tariff,
            required_by_block=required_by_block,
            submit_deadlines_by_block=submit_deadlines_by_block,
            tasks_by_id={task_id: task} if task is not None else None,
            task_submit_deadlines_by_task=(
                {task_id: task_submit_deadlines} if task_submit_deadlines else None
            ),
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
    # Сравнение работ: ответ — адрес картинки, преподавателю он ничего не
    # скажет. Переводим в «Работа №k» по месту в галерее.
    compare_images = get_images(db, [
        block.id for _a, block, _r, _t, _u in rows if block.block_type == BLOCK_COMPARE
    ])
    # Ход выбора по парам — проверяющим, не ученику (владелец 28.09.2026).
    compare_steps = get_compare_steps(db, [
        (block.id, student.id)
        for _a, block, _r, _t, student in rows if block.block_type == BLOCK_COMPARE
    ])
    chosen = {}
    for answer, _b, response, _t, _u in rows:
        chosen.setdefault(response.id, None)
    for response_id in list(chosen):
        chosen[response_id] = get_selected_options(db, response_id=response_id)
    # Оценки шкалы — текстом выбранного пункта (владелец 30.09.2026: шкала
    # стала типом ответа в опросе, и без чисел проверяющий видел одни
    # названия пунктов, не узнав, что ответил ученик).
    scale_scores = {
        response_id: get_selected_option_texts(db, response_id=response_id)
        for response_id in {
            response.id for _a, block, response, _t, _u in rows
            if block.block_type == BLOCK_SCALE
        }
    }

    items: list[dict] = []
    for answer, block, response, task, student in rows:
        if block.block_type == BLOCK_COMPARE:
            images = compare_images.get(block.id, [])
            pick_url = compare_pick_url(images)
            items.append({
                "answer_id": answer.id,
                "student_id": student.id,
                "student_name": student.name,
                "task_id": task.id,
                "task_title": task.title,
                "subject": task.subject,
                "question": block.title or block.body or "",
                "question_type": None,
                "text": "",
                "chosen": [compare_work_label(images, answer.text)],
                "correct": [compare_work_label(images, pick_url)] if pick_url else [],
                "compare_pick_url": pick_url,
                "compare_steps": [
                    {
                        "step": s.step,
                        "left_url": s.left_url,
                        "right_url": s.right_url,
                        "winner_url": s.winner_url,
                        "left_label": compare_work_label(images, s.left_url),
                        "right_label": compare_work_label(images, s.right_url),
                    }
                    for s in compare_steps.get((block.id, student.id), [])
                ],
                "reviewed": answer.reviewed_at is not None,
                "answered_at": response.updated_at,
            })
            continue
        picked = chosen.get(response.id, {}).get(block.id, set())
        scores = scale_scores.get(response.id, {})
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
            "chosen": [
                f"{o.text} — {scores[o.id]}"
                if block.block_type == BLOCK_SCALE and scores.get(o.id) else o.text
                for o in options.get(block.id, []) if o.id in picked
            ],
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
    # Новая версия работы требует новой оценки. История диалога сохраняется,
    # чтобы ученик и преподаватель видели причину пересдачи.
    submission.score = None
    submission.scored_at = None
    submission.scored_by_id = None
    # Пересдача закрывает «на доработке» и старый комментарий проверки —
    # иначе review_comment тут же снова блокирует правку следующей попытки
    # (block_work_reason), а флаг молча остаётся висеть на новой версии.
    submission.needs_revision = False
    submission.review_comment = None
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
    расхождение в правилах доступа между ними было бы дырой. Так же, как там,
    удалённые задания скрыты, а `week_end` — граница-исключение
    (`week_bounds` отдаёт следующий понедельник 00:00).
    """
    from app.models.tracker import TrackerTask
    from app.models.user import User

    query = (
        db.query(TaskBlockSubmission, TaskBlock, TrackerTask, User)
        .join(TaskBlock, TaskBlock.id == TaskBlockSubmission.block_id)
        .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
        .join(User, User.id == TaskBlockSubmission.user_id)
        .filter(TaskBlockSubmission.submitted_at.isnot(None))
        .filter(TrackerTask.deleted_at.is_(None))
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
        query = query.filter(TaskBlockSubmission.submitted_at < week_end)

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

    # Состояния блоков одним запросом: `get_state` на строку давал до 200
    # походов в базу. Выборка по двум `IN` шире нужной пары, ключ — пара.
    state_map: dict[tuple[int, int], TaskBlockState] = {}
    if rows:
        block_ids = {block.id for _, block, _, _ in rows}
        user_ids = {student.id for _, _, _, student in rows}
        for state in (
            db.query(TaskBlockState)
            .filter(
                TaskBlockState.block_id.in_(block_ids),
                TaskBlockState.user_id.in_(user_ids),
            )
            .all()
        ):
            state_map[(state.block_id, state.user_id)] = state
    # Сроки по тарифам — одним запросом на всю выборку, как в ленте.
    block_deadlines = get_submit_deadlines(db, list({block.id for _, block, _, _ in rows}))
    task_deadlines = get_task_submit_deadlines(db, list({task.id for _, _, task, _ in rows}))

    items = []
    for submission, block, task, student in rows:
        state = state_map.get((block.id, student.id))
        items.append({
            "submission_id": submission.id,
            "block_id": block.id,
            "task_id": task.id,
            "student_id": student.id,
            "task_title": task.title,
            "block_title": block.title or BLOCK_TYPE_LABELS.get(block.block_type, ""),
            "subject": block.subject or task.subject,
            "comment": submission.comment,
            "review_comment": submission.review_comment,
            "images": [i.image_s3_url for i in image_map.get(submission.id, [])],
            "overrun": timed_overrun(block, state),
            # Сдано после срока (владелец 30.09.2026): после срока принимают
            # только контрольную на время, но отметка общая для всех сдач.
            "late": completed_after_deadline(
                block, task, state, user_tariff=student.tariff,
                block_overrides=block_deadlines.get(block.id),
                task_overrides=task_deadlines.get(task.id),
            ),
            "reviewed": submission.reviewed_at is not None,
            "needs_revision": submission.needs_revision,
            "score": int(submission.score) if submission.score is not None else None,
            "submitted_at": submission.submitted_at,
        })
    return items
