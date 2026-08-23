"""Учебная программа: календарь по дням и служебные темы элементов.

День программы — не сущность, а координата: московская дата у `TrackerTask.due_at`.
Отдельная таблица «день» состояла бы ровно из даты и внешнего ключа, то есть из
того, что уже лежит в задаче.

Аудитория элемента хранится в **служебной теме** (`LearningTopic` с
`kind="program_item"`). Так доступ к ролику считает уже проверенный
`accessible_topic_ids`, и переписывать его не нужно. `opens_at` служебной темы —
понедельник недели, в которую попадает день элемента: владелец решил 20.08, что
ученик видит неделю целиком с днями и заданиями, а не открывает их по одному.
"""

import calendar
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.constants import TARIFFS
from app.models.homework import HomeworkAssignment
from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, LearningTopic
from app.models.tag import Tag
from app.models.tracker import (
    ITEM_HOMEWORK,
    ITEM_MOCK_EXAM,
    ITEM_VIDEO,
    SOURCE_HOMEWORK,
    SOURCE_SURVEY,
    TrackerTask,
)
from app.models.learning_video import LearningVideo
from app.services.survey import get_surveys, question_counts as survey_question_counts
from app.services.tags import get_all_tags
from app.services.tz import MSK_TZ, msk_midnight, today_msk
from app.services.video_topics import (
    count_topic_audience,
    create_topic,
    get_assignee_ids,
    get_tag_ids,
    set_topic_assignees,
    set_topic_tags,
)

WEEKDAY_LABELS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")

MONTH_NAMES = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)

WEEKDAY_FULL = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)


def weekday_full_ru(day: date) -> str:
    return WEEKDAY_FULL[day.weekday()]


def day_title_ru(day: date) -> str:
    """«21 августа 2026, четверг» — общий заголовок дня для staff- и student-страниц."""
    return f"{day.day} {MONTH_NAMES[day.month - 1]} {day.year}, {weekday_full_ru(day)}"


def parse_day_iso(raw: str) -> date | None:
    """`YYYY-MM-DD` → date, или None на мусоре. Исключение — забота вызывающего кода."""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def week_start(day: date) -> date:
    """Понедельник недели, в которую попадает день."""
    return day - timedelta(days=day.weekday())


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Границы московских суток в UTC: [начало дня, начало следующего).

    В UTC намеренно: колонка на проде `TIMESTAMPTZ`, а SQLite в тестах хранит
    время без таймзоны и сравнивает его как строку по стенным часам. Отдать
    границу с московским смещением значило бы сравнивать 22:00 UTC с «00:00»
    и терять вечерние элементы.
    """
    start = msk_midnight(day).astimezone(timezone.utc)
    end = msk_midnight(day + timedelta(days=1)).astimezone(timezone.utc)
    return start, end


def msk_date(value: datetime) -> date:
    """Московская дата у значения из базы.

    Колонка `TIMESTAMPTZ` приезжает в таймзоне сессии (в контейнере UTC), а
    SQLite в тестах отдаёт наивное время — его весь проект трактует как UTC.
    Наивное `value.date()` увело бы вечерние элементы на сутки назад.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MSK_TZ).date()


# ---------------------------------------------------------------------------
# Служебная тема элемента
# ---------------------------------------------------------------------------

def ensure_item_topic(
    db: Session, *, title: str, day: date, user_id: int
) -> LearningTopic:
    """Завести служебную тему под элемент программы.

    Тема сразу опубликована и открывается в понедельник своей недели: отдельной
    кнопки «опубликовать» у элемента нет, гейт — дата. Аудитория пустая, её
    задаёт следующий шаг мастера, и до этого элемент не увидит никто.
    """
    topic = create_topic(
        db,
        title=title[:200],
        opens_at=msk_midnight(week_start(day)),
        user_id=user_id,
        assign_to_all=False,
        kind=TOPIC_KIND_PROGRAM_ITEM,
    )
    topic.is_published = True
    db.flush()
    return topic


def set_item_audience(
    db: Session,
    topic: LearningTopic,
    *,
    assign_to_all: bool,
    tag_ids: list[int],
    assignee_ids: list[int],
) -> None:
    topic.assign_to_all = assign_to_all
    set_topic_tags(db, topic, [] if assign_to_all else tag_ids)
    set_topic_assignees(db, topic, [] if assign_to_all else assignee_ids)


def item_audience(db: Session, topic: LearningTopic) -> int:
    return count_topic_audience(
        db,
        assign_to_all=topic.assign_to_all,
        tag_ids=get_tag_ids(db, topic.id),
        assignee_ids=get_assignee_ids(db, topic.id),
    )


# ---------------------------------------------------------------------------
# Календарь
# ---------------------------------------------------------------------------

def month_days(year: int, month: int, today: date | None = None) -> list[dict]:
    """Дни месяца для сетки: день недели, выходной, сегодня, прошлое.

    Первый элемент списка — не первое число, а понедельник его недели: сетка
    рисуется целыми неделями, иначе тёмные ячейки выходных не разделят месяц.
    """
    today = today or today_msk()
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    cursor = week_start(first)
    days: list[dict] = []
    while cursor <= last or cursor.weekday() != 0:
        days.append(
            {
                "date": cursor,
                "iso": cursor.isoformat(),
                "number": cursor.day,
                "dow": cursor.isoweekday(),
                "is_weekend": cursor.isoweekday() >= 6,
                "is_today": cursor == today,
                "is_past": cursor < today,
                "in_month": cursor.month == month,
            }
        )
        cursor += timedelta(days=1)
    return days


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def month_marks(db: Session, year: int, month: int) -> dict[str, dict]:
    """Отметки в клетках: что уже стоит в каждом дне месяца.

    Группируем в Python по московской дате: `date_trunc` в SQL считался бы
    по-разному на SQLite в тестах и на Postgres в проде.
    """
    first = week_start(date(year, month, 1))
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    start, _ = day_bounds(first)
    _, end = day_bounds(last_day + timedelta(days=7))
    tasks = (
        db.query(TrackerTask)
        .filter(
            TrackerTask.deleted_at.is_(None),
            TrackerTask.due_at.isnot(None),
            TrackerTask.due_at >= start,
            TrackerTask.due_at < end,
        )
        .all()
    )
    marks: dict[str, dict] = {}
    for task in tasks:
        key = msk_date(task.due_at).isoformat()
        mark = marks.setdefault(
            key, {"mock": [], "video": 0, "homework": 0, "other": 0, "total": 0}
        )
        mark["total"] += 1
        if task.kind == ITEM_MOCK_EXAM:
            if task.subject and task.subject not in mark["mock"]:
                mark["mock"].append(task.subject)
            elif not task.subject:
                mark["mock"].append("")
        elif task.kind == ITEM_VIDEO:
            mark["video"] += 1
        elif task.kind == ITEM_HOMEWORK:
            mark["homework"] += 1
        else:
            mark["other"] += 1
    return marks


def items_for_day(db: Session, day: date) -> list[TrackerTask]:
    start, end = day_bounds(day)
    return (
        db.query(TrackerTask)
        .filter(
            TrackerTask.deleted_at.is_(None),
            TrackerTask.due_at >= start,
            TrackerTask.due_at < end,
        )
        .order_by(TrackerTask.sort_order.asc(), TrackerTask.id.asc())
        .all()
    )


def item_details(db: Session, tasks: list[TrackerTask]) -> dict[int, dict]:
    """Подробности элементов для экрана дня: домашка и ролик.

    Отдельным запросом на тип, а не по строке: элементов в дне единицы, но
    привычка ходить в базу внутри цикла шаблона дорого обходится позже.
    """
    homework_ids = [
        t.source_id for t in tasks if t.source_kind == SOURCE_HOMEWORK and t.source_id
    ]
    homework = {
        h.id: h
        for h in db.query(HomeworkAssignment)
        .filter(HomeworkAssignment.id.in_(homework_ids))
        .all()
    } if homework_ids else {}

    topic_ids = [t.topic_id for t in tasks if t.topic_id]
    videos: dict[int, LearningVideo] = {}
    if topic_ids:
        for video in (
            db.query(LearningVideo)
            .filter(
                LearningVideo.topic_id.in_(topic_ids),
                LearningVideo.deleted_at.is_(None),
            )
            .all()
        ):
            videos[video.topic_id] = video

    survey_ids = [
        t.source_id for t in tasks if t.source_kind == SOURCE_SURVEY and t.source_id
    ]
    surveys = get_surveys(db, survey_ids)
    survey_counts = survey_question_counts(db, list(surveys.keys()))

    details: dict[int, dict] = {}
    for task in tasks:
        survey = surveys.get(task.source_id) if task.source_kind == SOURCE_SURVEY else None
        details[task.id] = {
            "homework": homework.get(task.source_id) if task.source_kind == SOURCE_HOMEWORK else None,
            "video": videos.get(task.topic_id),
            "survey": survey,
            "survey_question_count": survey_counts.get(survey.id, 0) if survey else 0,
        }
    return details


def tags_split(db: Session) -> tuple[list[Tag], list[Tag]]:
    """Теги двумя списками: тарифные и остальные.

    Отдельной сущности тарифа в проекте нет — тариф лежит строкой у ученика, а
    одноимённые теги заводит `ensure_profile_tags`. Поэтому деление тут по имени.
    """
    tariff_names = {name.strip().lower() for name in TARIFFS}
    tariffs: list[Tag] = []
    others: list[Tag] = []
    for tag in get_all_tags(db):
        if (tag.name or "").strip().lower() in tariff_names:
            tariffs.append(tag)
        else:
            others.append(tag)
    return tariffs, others
