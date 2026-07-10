"""Access rules for mock-exam submission.

This module is the source of truth for:
1. exact per-ticket Moscow-time submission windows;
2. legacy daily window fallback for old tickets without opens_at/closes_at;
3. which mock-exam subjects are available to a student this week.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS
from app.models.tag import Tag, UserTag
from app.models.user import User
from app.services.tz import MSK_TZ, now_msk

if TYPE_CHECKING:
    from app.models.exam_assignment import ExamTicket

MOCK_EXAM_DURATION_SEC = 90 * 60
MOCK_EXAM_DEFAULT_DURATION_MINUTES = MOCK_EXAM_DURATION_SEC // 60
MOCK_EXAM_OPEN_AT = time(11, 45)
MOCK_EXAM_CLOSE_AT = time(18, 30)

DRAWING_SUBJECT = "Рисунок"
COMPOSITION_SUBJECT = "Композиция"


def _as_msk(value: datetime | None = None) -> datetime:
    if value is None:
        return now_msk()
    if value.tzinfo is None:
        return value.replace(tzinfo=MSK_TZ)
    return value.astimezone(MSK_TZ)


def ticket_opens_at(ticket: "ExamTicket") -> datetime:
    if ticket.opens_at is not None:
        return _as_msk(ticket.opens_at)
    return datetime.combine(ticket.start_date, MOCK_EXAM_OPEN_AT, tzinfo=MSK_TZ)


def ticket_closes_at(ticket: "ExamTicket") -> datetime:
    if ticket.closes_at is not None:
        return _as_msk(ticket.closes_at)
    return datetime.combine(ticket.end_date, MOCK_EXAM_CLOSE_AT, tzinfo=MSK_TZ)


def ticket_duration_sec(ticket: "ExamTicket") -> int:
    if ticket.duration_minutes is not None and ticket.duration_minutes > 0:
        return int(ticket.duration_minutes) * 60
    return MOCK_EXAM_DURATION_SEC


def ticket_latest_start_at(ticket: "ExamTicket") -> datetime:
    """Последний момент, когда можно ПОЛУЧИТЬ билет, если включён restrict_start_by_duration.

    = конец периода доступа (closes_at) минус «время на выполнение» (duration),
    чтобы у ученика оставалось полное время на работу внутри периода. Пример:
    период до 14:00, выполнение 90 мин → последний старт в 12:30. Сдавать при этом
    можно до самого closes_at (таймер визуальный, см. is_mock_exam_attempt_open).
    """
    return ticket_closes_at(ticket) - timedelta(seconds=ticket_duration_sec(ticket))


def ticket_start_cutoff_at(ticket: "ExamTicket") -> datetime:
    """Фактический последний момент получения билета с учётом флага
    restrict_start_by_duration: включён (по умолчанию для старых билетов, None
    трактуется как включён) — cutoff = latest_start_at; выключен — cutoff =
    closes_at (получить билет можно до самого конца периода)."""
    if ticket.restrict_start_by_duration is False:
        return ticket_closes_at(ticket)
    return ticket_latest_start_at(ticket)


def is_mock_exam_ticket_submission_open(
    ticket: "ExamTicket", value: datetime | None = None
) -> bool:
    """Сдача больше не ограничена ВЕРХНЕЙ границей периода (closes_at).

    Раньше после closes_at билет перестава́л считаться активным и сдача
    блокировалась — ученики, не успевшие сдать в окно (например, зависла
    сессия), теряли работу без возможности досдать. Теперь сдать билет можно в
    любой момент после его открытия (opens_at), пока задание не архивировано/
    не вышел следующий пробник (это и есть source of truth для get_active_ticket).
    Нижняя граница (opens_at) сохранена: билет с будущей датой выдачи не
    должен считаться активным заранее. Получение билета («Начать пробник»)
    отдельно ограничено окном opens_at..latest_start — см.
    is_mock_exam_ticket_start_open.
    """
    return _as_msk(value) >= ticket_opens_at(ticket)


def is_mock_exam_ticket_start_open(ticket: "ExamTicket", value: datetime | None = None) -> bool:
    """Получение билета («Начать пробник») открыто от opens_at до cutoff.

    Если restrict_start_by_duration включён (по умолчанию) — cutoff = latest_start
    (closes_at − duration): нельзя начинать, если до конца периода осталось
    меньше «времени на выполнение». Если выключен — cutoff = closes_at, получение
    не ограничено временем на выполнение (оно остаётся только визуальным счётчиком).
    Сдача при этом открыта до closes_at отдельно (см. is_mock_exam_ticket_submission_open)."""
    return ticket_opens_at(ticket) <= _as_msk(value) <= ticket_start_cutoff_at(ticket)


def mock_exam_deadline_for_started_at(
    started_at: datetime,
    closes_at: datetime | None = None,
    duration_sec: int = MOCK_EXAM_DURATION_SEC,
) -> datetime:
    start_msk = _as_msk(started_at)
    duration_deadline = start_msk + timedelta(seconds=duration_sec)
    close_msk = _as_msk(closes_at) if closes_at is not None else datetime.combine(
        start_msk.date(), MOCK_EXAM_CLOSE_AT, tzinfo=MSK_TZ
    )
    return min(duration_deadline, close_msk)


def is_mock_exam_attempt_open(
    started_at: datetime,
    value: datetime | None = None,
    closes_at: datetime | None = None,
    duration_sec: int = MOCK_EXAM_DURATION_SEC,
) -> bool:
    """Попытка остаётся открытой для сдачи без ограничения по времени.

    «Время на выполнение» (duration_sec / duration_minutes) — только визуальный
    счётчик: раньше попытка «протухала» через него (90 мин) или по closes_at, и
    ученики не могли сдать работу, не уложившись в таймер. Теперь таймер ничего
    не блокирует (mock_exam_deadline_for_started_at используется только для
    отображения отсчёта на странице). Доступ ограничивает ОТДЕЛЬНО период билета
    opens_at..closes_at через get_active_ticket / is_mock_exam_ticket_submission_open,
    поэтому вне периода доступа сдать всё равно нельзя.

    Параметры сохранены ради совместимости с вызывающим кодом.
    """
    return True


def mock_exam_window_error(*, for_start: bool, ticket: "ExamTicket | None" = None) -> str:
    if ticket is None:
        action = "Начать пробник" if for_start else "Сдать пробник"
        return f"{action} можно в период доступа, назначенный куратором"
    opens = ticket_opens_at(ticket)
    if for_start:
        cutoff = ticket_start_cutoff_at(ticket)
        if ticket.restrict_start_by_duration is False:
            return (
                f"Получить билет можно с {opens.strftime('%d.%m.%Y %H:%M')} по "
                f"{cutoff.strftime('%d.%m.%Y %H:%M')} МСК"
            )
        return (
            f"Получить билет можно с {opens.strftime('%d.%m.%Y %H:%M')} по "
            f"{cutoff.strftime('%d.%m.%Y %H:%M')} МСК (не позже чем за "
            f"{ticket_duration_sec(ticket) // 60} мин до конца периода)"
        )
    closes = ticket_closes_at(ticket)
    return (
        f"Сдать пробник можно в период доступа: с "
        f"{opens.strftime('%d.%m.%Y %H:%M')} по "
        f"{closes.strftime('%d.%m.%Y %H:%M')} МСК"
    )


def _subjects_from_marker(raw: str | None) -> set[str]:
    if not raw:
        return set()
    normalized = raw.strip().lower().replace(" ", "")
    subjects: set[str] = set()
    if not normalized:
        return subjects

    if normalized in {"рисунок", "drawing"} or "рисунок" in normalized:
        subjects.add(DRAWING_SUBJECT)
    if normalized in {"композиция", "composition"} or "композиция" in normalized:
        subjects.add(COMPOSITION_SUBJECT)
    compact_code = normalized.replace("+", "").replace("/", "").replace(",", "")
    if compact_code and set(compact_code) <= {"р", "к"}:
        if "р" in compact_code:
            subjects.add(DRAWING_SUBJECT)
        if "к" in compact_code:
            subjects.add(COMPOSITION_SUBJECT)
    return subjects


def _profile_subjects(student: User) -> set[str]:
    return _subjects_from_marker(student.exam_subjects)


def _user_tag_rows(db: DBSession, user_id: int) -> list[tuple[int, str]]:
    return (
        db.query(Tag.id, Tag.name)
        .join(UserTag, UserTag.tag_id == Tag.id)
        .filter(UserTag.user_id == user_id)
        .all()
    )


def get_matching_target_tag_ids_for_student(db: DBSession, user_id: int) -> set[int]:
    """Return target tag ids this student's tags can satisfy.

    Exact tag matches are always kept. For subject marker tags we also allow a
    wider student tag, e.g. "Р+К", to satisfy single-subject targets "Р" and "К".
    Non-subject tags intentionally remain exact-only.
    """
    user_tags = _user_tag_rows(db, user_id)
    matching_ids = {tag_id for tag_id, _ in user_tags}
    student_subjects: set[str] = set()
    for _, name in user_tags:
        student_subjects.update(_subjects_from_marker(name))

    if not student_subjects:
        return matching_ids

    for tag_id, name in db.query(Tag.id, Tag.name).all():
        target_subjects = _subjects_from_marker(name)
        if target_subjects and target_subjects.issubset(student_subjects):
            matching_ids.add(tag_id)
    return matching_ids


def is_target_tag_allowed_for_student(
    db: DBSession, user_id: int, target_tag_id: int | None
) -> bool:
    if target_tag_id is None:
        return True
    return target_tag_id in get_matching_target_tag_ids_for_student(db, user_id)


def get_student_ids_for_target_tag(
    db: DBSession, target_tag_id: int, *, role_id: int | None = None
) -> set[int]:
    """Return active student ids whose tags satisfy a ticket target tag."""
    target = db.query(Tag.name).filter(Tag.id == target_tag_id).first()
    if target is None:
        return set()
    target_subjects = _subjects_from_marker(target.name)

    query = (
        db.query(User.id, Tag.id, Tag.name)
        .join(UserTag, UserTag.user_id == User.id)
        .join(Tag, Tag.id == UserTag.tag_id)
        .filter(User.is_active == True)  # noqa: E712
    )
    if role_id is not None:
        query = query.filter(User.role_id == role_id)

    student_ids: set[int] = set()
    for user_id, tag_id, tag_name in query.all():
        if tag_id == target_tag_id:
            student_ids.add(user_id)
            continue
        if target_subjects and target_subjects.issubset(_subjects_from_marker(tag_name)):
            student_ids.add(user_id)
    return student_ids


def get_allowed_mock_subjects(db: DBSession, user_id: int) -> list[str]:
    """Return subjects available to the student.

    Только явное поле профиля (exam_subjects) может сузить список — теги
    ученика больше не используются для угадывания предмета. Раньше тег вида
    «Р»/«К» трактовался как маркер предмета (см. _subjects_from_marker), но в
    проде такие однобуквенные теги массово используются для другого (группа/
    уровень куратора) и не имеют отношения к предмету — это случайно прятало
    билеты по другому предмету от учеников. Доступ к конкретному билету и так
    отдельно фильтруется его собственным target_tag_id в get_active_ticket:
    если у билета тег не задан, он открыт всем без исключения.
    """
    student = db.query(User).filter(User.id == user_id).first()
    if not student:
        return []

    allowed = _profile_subjects(student)
    if not allowed:
        allowed = set(MOCK_SUBJECTS)

    return [subject for subject in MOCK_SUBJECTS if subject in allowed]


def is_subject_allowed_for_student(db: DBSession, user_id: int, subject: str) -> bool:
    return subject in get_allowed_mock_subjects(db, user_id)
