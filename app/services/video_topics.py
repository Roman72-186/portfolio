"""Темы недели видеомодуля: доступ учеников и управление из админки.

Source of truth по тому, какие темы открыты ученику. С пробниками не связано
намеренно — `mock_exam_access` здесь не используется, сопоставление тегов строгое
по `tag_id`. Предметная эвристика пробников (однобуквенные «Р»/«К» как маркеры
предмета) на видеоуроки не распространяется: в проде эти теги означают группу и
уровень куратора, и на билетах она уже прятала задания от учеников.
"""

from datetime import date, datetime, timezone

from sqlalchemy import or_, true
from sqlalchemy.orm import Session

from app.constants import REPORT_EXCLUDED_USER_IDS
from app.models.learning_topic import (
    TOPIC_KIND_WEEK,
    LearningTopic,
    LearningTopicAssignee,
    LearningTopicTag,
    LearningTopicTariff,
)
from app.models.role import Role
from app.models.tag import Tag, UserTag
from app.models.user import User
from app.services.tz import MSK_TZ, now_msk

STUDENT_ROLE_RANK = 1

# Однобуквенные «Р» и «К» (и их склейки вроде «Р+К») в проде означают группу и
# уровень куратора, а в модуле пробников такие же имена трактуются как маркеры
# предмета. Здесь сопоставление строгое: тема, адресованная тегу «Р», не дойдёт
# до учеников с тегом «Р+К» — главный преподаватель обычно ждёт обратного.
# Значение намеренно не импортируется из mock_exam_access: это подсказка ему, а не
# правило доступа,
# и связь видеомодуля с пробниками остаётся разорванной.
AMBIGUOUS_TAG_LETTERS = frozenset("рк")


def list_topics(
    db: Session,
    *,
    include_deleted: bool = False,
    kinds: tuple[str, ...] | None = (TOPIC_KIND_WEEK,),
) -> list[LearningTopic]:
    """Темы для списков и выпадающих меню.

    По умолчанию отдаются только темы недель: служебные темы элементов учебной
    программы человек не заводил и выбирать их ему незачем. `kinds=None` снимает
    фильтр целиком.
    """
    query = db.query(LearningTopic)
    if not include_deleted:
        query = query.filter(LearningTopic.deleted_at.is_(None))
    if kinds is not None:
        query = query.filter(LearningTopic.kind.in_(kinds))
    return query.order_by(
        LearningTopic.sort_order.asc(), LearningTopic.opens_at.desc()
    ).all()


def get_topic(
    db: Session, topic_id: int, *, kinds: tuple[str, ...] | None = None
) -> LearningTopic | None:
    """Тема по id. `kinds` сужает вид — например, чтобы экран недель не открылся
    на служебной теме элемента программы."""
    topic = db.get(LearningTopic, topic_id)
    if topic is None or topic.deleted_at is not None:
        return None
    if kinds is not None and topic.kind not in kinds:
        return None
    return topic


def accessible_topic_ids(db: Session, user_id: int) -> set[int]:
    """Темы, открытые ученику прямо сейчас.

    Тема открыта, если опубликована, наступил её `opens_at`, адресована
    ученику (флагом «всем», пересечением тегов или поимённо), не скрыта по
    тарифу и не закончилась до его прихода (`ended_before_arrival_filter`). Это ученический контракт функции: тарифный фильтр здесь
    сознательно не выключается никаким аргументом — куратор/staff читают
    элементы дня напрямую (`program.py::items_for_day`/`item_details`), в
    обход этой функции, и видят их независимо от тарифа (владелец 26.08.2026).

    Верхней границы у окна нет: прошедшая тема остаётся в каталоге как учебный
    архив. Время берём через `tz.now_msk()` — в контейнере UTC, иначе фильтр
    уезжает на три часа.
    """
    user_tag_ids = (
        db.query(UserTag.tag_id).filter(UserTag.user_id == user_id).scalar_subquery()
    )
    tagged_topic_ids = (
        db.query(LearningTopicTag.topic_id)
        .filter(LearningTopicTag.tag_id.in_(user_tag_ids))
        .scalar_subquery()
    )
    assigned_topic_ids = (
        db.query(LearningTopicAssignee.topic_id)
        .filter(LearningTopicAssignee.user_id == user_id)
        .scalar_subquery()
    )
    # Тариф ученика — плоская строка, не EncryptedString, сравнивать в SQL
    # можно напрямую. Пустой/None тариф просто не совпадёт ни с одной строкой
    # LearningTopicTariff — тарифно-ограниченные темы остаются скрытыми.
    row = (
        db.query(User.tariff, User.program_access_from)
        .filter(User.id == user_id)
        .first()
    )
    tariff, program_access_from = row if row is not None else (None, None)
    tariff = (tariff or "").strip().upper()
    now = now_msk()
    # Окно тарифа (владелец 06.10.2026): до «с» цикла у тарифа нет. «По» здесь
    # не проверяется — после него цикл уходит в архив, а не пропадает
    # (`tariff_closed_topic_ids`).
    tariff_ok_topic_ids = (
        db.query(LearningTopicTariff.topic_id)
        .filter(
            LearningTopicTariff.tariff == tariff,
            or_(
                LearningTopicTariff.opens_at.is_(None),
                LearningTopicTariff.opens_at <= now,
            ),
        )
        .scalar_subquery()
    )
    rows = (
        db.query(LearningTopic.id)
        .filter(
            LearningTopic.deleted_at.is_(None),
            LearningTopic.is_published.is_(True),
            LearningTopic.opens_at <= now,
            or_(
                LearningTopic.assign_to_all.is_(True),
                LearningTopic.id.in_(tagged_topic_ids),
                LearningTopic.id.in_(assigned_topic_ids),
            ),
            or_(
                LearningTopic.tariff_restricted.is_(False),
                LearningTopic.id.in_(tariff_ok_topic_ids),
            ),
            ended_before_arrival_filter(program_access_from),
        )
        .all()
    )
    return {row[0] for row in rows}


def saw_topic_period(program_access_from: datetime | None, topic: LearningTopic) -> bool:
    """То же правило, что `ended_before_arrival_filter`, для одного ученика в
    Python — там, где ученики уже прочитаны списком (статистика цикла)."""
    if program_access_from is None or topic.ends_at is None:
        return True
    ends_at = topic.ends_at
    # SQLite в тестах отдаёт наивное время — нормализация как в
    # `topic_audience_user_ids` выше.
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    if program_access_from.tzinfo is None:
        program_access_from = program_access_from.replace(tzinfo=timezone.utc)
    return ends_at >= program_access_from


def ended_before_arrival_filter(program_access_from: datetime | None):
    """Условие SQL «тема не закончилась до прихода ученика».

    Владелец 29.09.2026: новые ученики годового курса не видят циклы
    предобучения, за которые не платили, — а текущий цикл, его этап
    («Портфолио») и всё дальнейшее видят. Граница — `User.program_access_from`
    (ставит `user_management.open_program_from_now`): тема, чей `ends_at`
    раньше неё, ученику закрыта. NULL у ученика — видно всё (все, кто учился
    до 29.09.2026); NULL у темы — у неё нет конца (каталог видео, служебные
    темы календаря), и она не прячется никогда.

    Правило одно на все слои: лента, карусель циклов, трекер, видео, гейты
    «цикл пройден» и статистика цикла берут его отсюда, своей копии не держат.
    """
    if program_access_from is None:
        return true()
    return or_(
        LearningTopic.ends_at.is_(None),
        LearningTopic.ends_at >= program_access_from,
    )


def topic_audience_user_ids(db: Session, topic_id: int) -> set[int]:
    """Активные ученики, которым открыта конкретная тема — зеркало
    `accessible_topic_ids` (по ученику), но по теме: те же условия
    (опубликована, наступил `opens_at`, адресация, тариф), без фильтра по
    членству в группе — им управляет `require_learning_content_access`,
    отдельно от того, видна ли тема вообще.

    Нужен статистике прохождения диагностики (`archi_profile_stats.py`):
    почти любая диагностика заведена внутри цикла/дня и получает свою
    аудиторию не из `TrackerTask.assign_to_all`, а из темы, к которой
    привязана (`_accessible_task_or_404` в `cabinet_tracker.py` проверяет
    именно `task.topic_id in accessible_topic_ids(...)`).
    """
    topic = db.get(LearningTopic, topic_id)
    if topic is None or topic.deleted_at is not None or not topic.is_published:
        return set()
    opens_at = topic.opens_at
    # SQLite в тестах отдаёт наивное время из колонки DateTime(timezone=True) —
    # тот же приём нормализации, что в `tracker.py::task_status`.
    if opens_at.tzinfo is None:
        opens_at = opens_at.replace(tzinfo=timezone.utc)
    if opens_at > now_msk():
        return set()
    students = (
        db.query(User.id)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == STUDENT_ROLE_RANK,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    if topic.ends_at is not None:
        # Зеркало `ended_before_arrival_filter`: пришедший после конца темы
        # её не видит и в её аудиторию не входит.
        students = students.filter(or_(
            User.program_access_from.is_(None),
            User.program_access_from <= topic.ends_at,
        ))
    if topic.tariff_restricted:
        # Только тарифы, чьё окно уже открылось, — зеркало `accessible_topic_ids`.
        now = now_msk()
        tariffs = [
            tariff for tariff, (window_opens, _) in get_topic_tariff_windows(db, topic_id).items()
            if window_opens is None or _aware(window_opens) <= now
        ]
        students = students.filter(User.tariff.in_(tariffs or ()))

    if topic.assign_to_all:
        return {row[0] for row in students.all()}

    tag_ids = [
        row[0] for row in
        db.query(LearningTopicTag.tag_id).filter(LearningTopicTag.topic_id == topic_id).all()
    ]
    assignee_ids = [
        row[0] for row in
        db.query(LearningTopicAssignee.user_id).filter(LearningTopicAssignee.topic_id == topic_id).all()
    ]
    reached: set[int] = set()
    if tag_ids:
        rows = (
            students.join(UserTag, UserTag.user_id == User.id)
            .filter(UserTag.tag_id.in_(tag_ids))
            .all()
        )
        reached.update(row[0] for row in rows)
    if assignee_ids:
        rows = students.filter(User.id.in_(assignee_ids)).all()
        reached.update(row[0] for row in rows)
    return reached


def create_topic(
    db: Session,
    *,
    title: str,
    opens_at: datetime,
    user_id: int,
    description: str | None = None,
    assign_to_all: bool = False,
    kind: str = TOPIC_KIND_WEEK,
    ends_at: datetime | None = None,
    parent_id: int | None = None,
) -> LearningTopic:
    topic = LearningTopic(
        title=title,
        description=description,
        opens_at=opens_at,
        ends_at=ends_at,
        assign_to_all=assign_to_all,
        kind=kind,
        created_by_id=user_id,
        parent_id=parent_id,
    )
    db.add(topic)
    db.flush()
    return topic


def update_topic(
    topic: LearningTopic,
    *,
    title: str,
    opens_at: datetime,
    description: str | None = None,
    assign_to_all: bool = False,
    sort_order: int | None = None,
    ends_at: datetime | None = None,
    parent_id: int | None = None,
    set_parent: bool = False,
) -> None:
    topic.title = title
    topic.description = description
    topic.opens_at = opens_at
    # Пустое поле в форме — снять конец периода, а не сохранить прежний:
    # иначе цикл нельзя было бы вернуть к обычной неделе.
    topic.ends_at = ends_at
    topic.assign_to_all = assign_to_all
    if sort_order is not None:
        topic.sort_order = sort_order
    # `set_parent` — явный флаг, а не «parent_id is not None значит менять»:
    # иначе снять у цикла этап (перевести обратно в бесхозный архив) было бы
    # нечем, `None` в вызове читался бы как «не трогать».
    if set_parent:
        topic.parent_id = parent_id


def set_topic_tags(db: Session, topic: LearningTopic, tag_ids: list[int]) -> None:
    """Переписать адресацию по тегам целиком."""
    db.query(LearningTopicTag).filter(LearningTopicTag.topic_id == topic.id).delete(
        synchronize_session=False
    )
    for tag_id in dict.fromkeys(tag_ids):
        db.add(LearningTopicTag(topic_id=topic.id, tag_id=tag_id))
    db.flush()


def set_topic_assignees(db: Session, topic: LearningTopic, user_ids: list[int]) -> None:
    """Переписать поимённые исключения целиком."""
    db.query(LearningTopicAssignee).filter(
        LearningTopicAssignee.topic_id == topic.id
    ).delete(synchronize_session=False)
    for user_id in dict.fromkeys(user_ids):
        db.add(LearningTopicAssignee(topic_id=topic.id, user_id=user_id))
    db.flush()


def get_tag_ids(db: Session, topic_id: int) -> list[int]:
    rows = (
        db.query(LearningTopicTag.tag_id)
        .filter(LearningTopicTag.topic_id == topic_id)
        .all()
    )
    return [row[0] for row in rows]


def get_assignee_ids(db: Session, topic_id: int) -> list[int]:
    rows = (
        db.query(LearningTopicAssignee.user_id)
        .filter(LearningTopicAssignee.topic_id == topic_id)
        .all()
    )
    return [row[0] for row in rows]


def get_topic_tariffs(db: Session, topic_id: int) -> list[str]:
    rows = (
        db.query(LearningTopicTariff.tariff)
        .filter(LearningTopicTariff.topic_id == topic_id)
        .all()
    )
    return [row[0] for row in rows]


def set_topic_tariffs(
    db: Session, topic: LearningTopic, *, tariff_restricted: bool, tariffs: list[str]
) -> None:
    """Переписать тарифную видимость целиком — та же семантика, что у
    `set_topic_tags`. `tariff_restricted=False` чистит строки, а не оставляет
    их висеть неиспользуемыми: включили ограничение заново — список тарифов
    не должен внезапно вернуться из прошлого состояния."""
    topic.tariff_restricted = tariff_restricted
    db.query(LearningTopicTariff).filter(
        LearningTopicTariff.topic_id == topic.id
    ).delete(synchronize_session=False)
    if tariff_restricted:
        for tariff in dict.fromkeys(t.strip().upper() for t in tariffs if t.strip()):
            db.add(LearningTopicTariff(topic_id=topic.id, tariff=tariff))
    db.flush()


TariffWindow = tuple[datetime | None, datetime | None]


def _aware(value: datetime) -> datetime:
    """SQLite в тестах теряет зону у `DateTime(timezone=True)` — читаем как UTC,
    тем же приёмом, что `saw_topic_period`."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def get_topic_tariff_windows(db: Session, topic_id: int) -> dict[str, TariffWindow]:
    """Тарифы темы с окном доступа: `{тариф: (с, по)}`, `None` — без границы.

    Окно заводит форма цикла (владелец 06.10.2026). У прочих тем обе границы
    пустые, и словарь просто повторяет `get_topic_tariffs`.
    """
    rows = (
        db.query(
            LearningTopicTariff.tariff,
            LearningTopicTariff.opens_at,
            LearningTopicTariff.closes_at,
        )
        .filter(LearningTopicTariff.topic_id == topic_id)
        .all()
    )
    return {tariff: (opens_at, closes_at) for tariff, opens_at, closes_at in rows}


def set_topic_tariff_windows(
    db: Session, topic: LearningTopic, windows: dict[str, TariffWindow] | None
) -> None:
    """Переписать доступность цикла по тарифам целиком.

    `None` — ограничения нет, цикл видят все тарифы в его общие даты. Словарь —
    цикл видят только перечисленные тарифы, каждый в своём окне; пустой
    словарь — скрыт от всех, та же договорённость, что у `tariff_restricted`
    (владелец 30.08.2026). Строки пишет тот же путь, что `set_topic_tariffs`:
    сначала снести, потом вставить, хвостов прошлого состояния не остаётся.
    """
    topic.tariff_restricted = windows is not None
    db.query(LearningTopicTariff).filter(
        LearningTopicTariff.topic_id == topic.id
    ).delete(synchronize_session=False)
    for tariff, (opens_at, closes_at) in (windows or {}).items():
        db.add(LearningTopicTariff(
            topic_id=topic.id, tariff=tariff, opens_at=opens_at, closes_at=closes_at,
        ))
    db.flush()


def tariff_window_closed(window: TariffWindow, today: date) -> bool:
    """Окно тарифа закончилось: день «по» уже прошёл (московские даты)."""
    closes_at = window[1]
    return closes_at is not None and _aware(closes_at).astimezone(MSK_TZ).date() < today


def tariff_closed_topic_ids(db: Session, user_id: int, today: date) -> set[int]:
    """Темы, у которых окно тарифа ученика уже закончилось (владелец 06.10.2026:
    после «по» цикл у тарифа — в архиве).

    Тема при этом остаётся в `accessible_topic_ids`: архиву и карусели нужен
    сам цикл. Что «закончилось» значит для ленты, долга и записи, решают
    `tracker.effective_cycle`/`cycle_debt` и `cycle_feed`, спрашивая отсюда.
    """
    tariff = (
        db.query(User.tariff).filter(User.id == user_id).scalar() or ""
    ).strip().upper()
    if not tariff:
        return set()
    rows = (
        db.query(LearningTopicTariff.topic_id, LearningTopicTariff.closes_at)
        .join(LearningTopic, LearningTopic.id == LearningTopicTariff.topic_id)
        .filter(
            LearningTopicTariff.tariff == tariff,
            LearningTopicTariff.closes_at.is_not(None),
            LearningTopic.tariff_restricted.is_(True),
        )
        .all()
    )
    return {
        topic_id for topic_id, closes_at in rows
        if tariff_window_closed((None, closes_at), today)
    }


def ambiguous_tag_names(db: Session, tag_ids: list[int]) -> list[str]:
    """Имена выбранных тегов, которые легко понять не так.

    Возвращает теги вида «Р», «К», «Р+К» — см. AMBIGUOUS_TAG_LETTERS. Доступ они
    не меняют, но главный преподаватель, который ждёт «все, кто учит рисунок»,
    получит только
    точное совпадение по тегу.
    """
    if not tag_ids:
        return []
    rows = db.query(Tag.name).filter(Tag.id.in_(tag_ids)).all()
    names = []
    for (name,) in rows:
        compact = (name or "").strip().lower()
        for separator in (" ", "+", "/", ",", "-"):
            compact = compact.replace(separator, "")
        if compact and set(compact) <= AMBIGUOUS_TAG_LETTERS:
            names.append(name)
    return names


def count_topic_audience(
    db: Session,
    *,
    assign_to_all: bool,
    tag_ids: list[int],
    assignee_ids: list[int],
    tariff_restricted: bool = False,
    tariffs: list[str] | None = None,
) -> int:
    """Сколько активных учеников реально получат тему.

    Считается по той же адресации, что и в accessible_topic_ids, и служит
    проверкой на глаз: адресовал теме «Р» и увидел трёх человек вместо сорока —
    значит выбран не тот тег. `tariff_restricted`/`tariffs` сужают охват так же,
    как в accessible_topic_ids — иначе цифра на созданном с тарифным
    ограничением элементе будет враньём.

    Учитывается и членство в группе: без него `require_learning_content_access`
    отдаёт ученику 403 на весь видеомодуль, и такой человек в охвате — обман.
    """
    students = (
        db.query(User.id)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == STUDENT_ROLE_RANK,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.is_group_member.is_(True),
            User.id.notin_(REPORT_EXCLUDED_USER_IDS),  # счётчик — учёт, служебных не считаем
        )
    )
    if tariff_restricted:
        tariff_values = [t.strip().upper() for t in (tariffs or []) if t.strip()]
        # Список тарифов пуст — оператору IN нечего сопоставлять, а
        # `User.tariff.in_([])` на некоторых диалектах ведёт себя странно;
        # `in_(())` даёт заведомо ложное условие на любом бэкенде.
        students = students.filter(User.tariff.in_(tariff_values or ()))

    if assign_to_all:
        return students.count()

    reached: set[int] = set()
    if tag_ids:
        rows = (
            students.join(UserTag, UserTag.user_id == User.id)
            .filter(UserTag.tag_id.in_(tag_ids))
            .all()
        )
        reached.update(row[0] for row in rows)
    if assignee_ids:
        rows = students.filter(User.id.in_(assignee_ids)).all()
        reached.update(row[0] for row in rows)
    return len(reached)


def publish_topic(topic: LearningTopic, *, user_id: int) -> None:
    """Повторная публикация уже опубликованной темы ничего не меняет. Формы
    этапа и цикла шлют `is_published` при каждом сохранении, и новая
    `published_at` выдавала тему за только что открытую: 05.10.2026 правка
    этапа «Предобучение» разослала 90 ученикам «Новый видеоурок» про
    задание, открытое с 16.09 (`student_reminders._task_opened_at`)."""
    if topic.deleted_at is not None:
        raise ValueError("Topic is deleted")
    if topic.is_published:
        return
    topic.is_published = True
    topic.published_at = datetime.now(timezone.utc)
    topic.published_by_id = user_id


def unpublish_topic(topic: LearningTopic) -> None:
    topic.is_published = False
    topic.published_at = None
    topic.published_by_id = None


def delete_topic(topic: LearningTopic) -> None:
    """Мягкое удаление. Уроки темы остаются, их topic_id обнулит FK ON DELETE
    только при физическом удалении — здесь связь сохраняется, но тема пропадает
    из выдачи, потому что accessible_topic_ids фильтрует по deleted_at."""
    topic.deleted_at = datetime.now(timezone.utc)
    topic.is_published = False
