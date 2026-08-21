"""Билеты пробника для учебной программы.

Правила окна билета здесь **повторены**, а не вынесены из
`app/api/cabinet_superadmin.py::_build_tickets_from_form`: та функция читает поля
формы (`ticket_1_opens_at` и подобные), а календарь шлёт JSON, и подделывать
словарь формы ради переиспользования — плохой шов. Через старую форму идёт
боевое создание пробников, поэтому её не трогаем. Цена решения: правило,
изменённое в одном месте, надо менять и во втором.

Два отличия от старой формы, оба намеренные:

* **аудитория никогда не становится «всем» сама по себе.** В форме
  `assign_all = target_tag_id is None and (t_all or not student_ids)`: забыл
  выбрать получателей — билет уехал всей школе. Здесь «всем» только по явному
  флагу.
* **период сдачи не открывается задним числом** — см. `ensure_mock_period_for`.
"""

from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import ASSIGNMENT_KIND_LABELS, FEATURE_MOCK_EXAM
from app.models.exam_assignment import (
    ExamAssignment,
    ExamTicket,
    ExamTicketAssignee,
    ExamTicketTag,
)
from app.models.feature_period import FeaturePeriod
from app.models.tag import Tag
from app.services.feature_periods import invalidate_feature_cache
from app.services.mock_exam_access import (
    MOCK_EXAM_CLOSE_AT,
    MOCK_EXAM_DEFAULT_DURATION_MINUTES,
    MOCK_EXAM_OPEN_AT,
    get_student_ids_for_target_tag,
)
from app.services.tz import MSK_TZ, today_msk

MAX_TICKETS_PER_SUBJECT = 10


def default_schedule_for_day(day: date) -> dict:
    """Окно по умолчанию: 11:45–18:30 выбранного дня, 90 минут на работу.

    Отличается от `_default_ticket_schedule` старой формы тем, что не сдвигает
    день на завтра: в календаре день уже выбран человеком.
    """
    return {
        "opens_at": datetime.combine(day, MOCK_EXAM_OPEN_AT).strftime("%Y-%m-%dT%H:%M"),
        "closes_at": datetime.combine(day, MOCK_EXAM_CLOSE_AT).strftime("%Y-%m-%dT%H:%M"),
        "duration_minutes": MOCK_EXAM_DEFAULT_DURATION_MINUTES,
    }


def parse_msk_datetime(raw: str, *, ticket_number: int, field_label: str) -> datetime:
    """Строка из формы (без таймзоны) — московское время. Возвращает UTC."""
    try:
        value = datetime.fromisoformat((raw or "").strip())
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Билет {ticket_number}: неверное время «{field_label}»",
        )
    if value.tzinfo is None:
        value = value.replace(tzinfo=MSK_TZ)
    return value.astimezone(timezone.utc)


def validate_window(
    *,
    ticket_number: int,
    opens_at: datetime,
    closes_at: datetime,
    duration_minutes: int,
    restrict_start_by_duration: bool,
) -> tuple[date, date]:
    """Проверить окно билета и вернуть legacy-границы `start_date`/`end_date`.

    Колонки дат NOT NULL, поэтому выводим их из точного окна, как это делает
    старая форма.
    """
    if duration_minutes < 1 or duration_minutes > 720:
        raise HTTPException(
            status_code=422,
            detail=f"Билет {ticket_number}: время на работу должно быть от 1 до 720 минут",
        )
    opens_msk = opens_at.astimezone(MSK_TZ)
    closes_msk = closes_at.astimezone(MSK_TZ)
    if closes_at <= opens_at:
        raise HTTPException(
            status_code=422,
            detail=f"Билет {ticket_number}: закрытие должно быть позже открытия",
        )
    if (
        restrict_start_by_duration
        and closes_msk - timedelta(minutes=duration_minutes) < opens_msk
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Билет {ticket_number}: окно короче времени на работу "
                f"({duration_minutes} мин) — билет нельзя будет получить"
            ),
        )
    if closes_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=422, detail=f"Билет {ticket_number}: закрытие уже в прошлом"
        )
    return opens_msk.date(), closes_msk.date()


def next_seq_number(db: Session, kind: str, subject: str) -> int:
    """Сквозной номер задания в пределах (kind, subject): MAX+1.

    Копия `cabinet_superadmin._next_seq_number`: нумерация общая со старой
    формой, иначе в списке заданий появятся два «Пробника №7» по одному предмету.
    """
    current = (
        db.query(func.max(ExamAssignment.seq_number))
        .filter(ExamAssignment.kind == kind, ExamAssignment.subject == subject)
        .scalar()
    )
    return (current or 0) + 1


def compose_assignment_title(
    kind: str, seq: int | None, subject: str, created: date, note: str | None = None
) -> str:
    """«Пробник №5 · Рисунок · 22.06.2026 · примечание»."""
    label = ASSIGNMENT_KIND_LABELS.get(kind, ASSIGNMENT_KIND_LABELS["mock"])
    head = f"{label} №{seq}" if seq else label
    parts = [head, subject, created.strftime("%d.%m.%Y")]
    if note:
        parts.append(note)
    return " · ".join(parts)


def create_ticket(
    db: Session,
    assignment: ExamAssignment,
    *,
    number: int,
    title: str,
    description: str | None,
    image_url: str | None,
    image_path: str | None,
    opens_at: datetime,
    closes_at: datetime,
    duration_minutes: int,
    restrict_start_by_duration: bool,
    start_date: date,
    end_date: date,
    assign_to_all: bool,
    tag_ids: list[int],
    assignee_ids: list[int],
) -> ExamTicket:
    """Создать билет и разложить адресацию по трём местам.

    `target_tag_id` — первый тег: его читает планировщик уведомлений. Полный
    список тегов идёт в `exam_ticket_tags`, откуда доступ и считается. Носители
    тегов вдобавок материализуются в `ExamTicketAssignee`, иначе рассылка о
    завтрашнем пробнике до них не дойдёт.
    """
    ticket = ExamTicket(
        assignment_id=assignment.id,
        ticket_number=number,
        title=title,
        description=description,
        image_s3_url=image_url,
        image_s3_path=image_path,
        start_date=start_date,
        end_date=end_date,
        opens_at=opens_at,
        closes_at=closes_at,
        duration_minutes=duration_minutes,
        restrict_start_by_duration=restrict_start_by_duration,
        target_tag_id=tag_ids[0] if tag_ids and not assign_to_all else None,
        assign_to_all=assign_to_all,
    )
    db.add(ticket)
    db.flush()

    if assign_to_all:
        return ticket

    reached: set[int] = set(assignee_ids)
    for tag_id in dict.fromkeys(tag_ids):
        db.add(ExamTicketTag(ticket_id=ticket.id, tag_id=tag_id))
        reached.update(get_student_ids_for_target_tag(db, tag_id))
    for user_id in reached:
        db.add(ExamTicketAssignee(ticket_id=ticket.id, user_id=user_id))
    db.flush()
    return ticket


def validate_tags(db: Session, tag_ids: list[int]) -> list[int]:
    if not tag_ids:
        return []
    found = {
        row[0] for row in db.query(Tag.id).filter(Tag.id.in_(tag_ids)).all()
    }
    missing = [tag_id for tag_id in tag_ids if tag_id not in found]
    if missing:
        raise HTTPException(status_code=422, detail="Выбранный тег не найден")
    return list(dict.fromkeys(tag_ids))


def ensure_mock_period_for(
    db: Session, *, start_date: date, end_date: date, user_id: int
) -> None:
    """Открыть период сдачи пробников ровно на окно билета.

    Отличие от `cabinet_superadmin._ensure_mock_exam_period_open`: тот считает
    период от **сегодня** до самой дальней даты по всем заданиям. Для пробника,
    поставленного на следующую неделю, это открыло бы ученикам сегодня и все
    старые билеты — у `is_mock_exam_ticket_submission_open` верхней границы нет,
    и период остаётся единственным замком.
    """
    today = today_msk()
    existing = (
        db.query(FeaturePeriod)
        .filter(
            FeaturePeriod.feature == FEATURE_MOCK_EXAM,
            FeaturePeriod.is_active.is_(True),
            FeaturePeriod.start_date <= start_date,
            FeaturePeriod.end_date >= start_date,
        )
        .first()
    )
    if existing:
        if existing.end_date < end_date:
            existing.end_date = end_date
            db.flush()
            if existing.start_date <= today <= existing.end_date:
                invalidate_feature_cache(FEATURE_MOCK_EXAM)
        return

    db.add(
        FeaturePeriod(
            feature=FEATURE_MOCK_EXAM,
            title="Открыто из учебной программы",
            start_date=start_date,
            end_date=end_date,
            is_active=True,
            created_by_id=user_id,
        )
    )
    db.flush()
    if start_date <= today <= end_date:
        invalidate_feature_cache(FEATURE_MOCK_EXAM)
