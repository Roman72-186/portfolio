"""Бизнес-логика гостевого пробника — ВРЕМЕННЫЙ модуль, см. app/models/guest_exam.py.

Сессия гостя не проходит через app.dependencies/Session/User — отдельный подписанный
cookie (guest-v1), отдельный CSRF-контекст (переиспользует app.csrf с cookie-значением
вместо session_id, см. app/api/guest_exam.py::require_guest_csrf).
"""
import random
import secrets
from datetime import date, datetime, timedelta, timezone

from itsdangerous import URLSafeTimedSerializer, BadData
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from sqlalchemy import func

from app.config import settings
from app.constants import MOCK_SUBJECTS
from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.guest_exam import (
    GuestExamConfig,
    GuestParticipant,
    GuestSubmission,
    GuestVisit,
)
from app.services.tz import MSK_TZ, today_msk

GUEST_ASSIGNMENT_KIND = "guest"


class TicketLockedError(Exception):
    """Гость взял билет по одному предмету и ещё не загрузил работу — билет по
    второму предмету не выдаётся, пока первый не сдан (решение владельца,
    25.08.2026). В атрибуте `subject` — предмет, который держит блокировку."""

    def __init__(self, subject: str):
        super().__init__(subject)
        self.subject = subject

# Билеты гостевого режима не имеют окна времени (решение владельца, 18.08.2026:
# «билет всегда доступен, пока включена ссылка») — end_date подставляется на
# 10 лет вперёд просто чтобы удовлетворить NOT NULL колонки ExamTicket,
# резолвер `issue_ticket` ниже дату вообще не проверяет.
_FAR_FUTURE_DAYS = 3650

GUEST_COOKIE_NAME = "guest_session"
# Ссылка бессрочная — участник может вернуться посмотреть балл в любой момент,
# поэтому cookie живёт по максимуму (полгода), а не привязана к окну приёма.
COOKIE_MAX_AGE = 180 * 24 * 3600

# Алфавит без спутываемых символов (0/O, 1/I).
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
_CODE_GENERATION_ATTEMPTS = 10

# Только визуальный отсчёт на странице — не блокирует отправку после истечения
# (тот же принцип, что у реального пробника, см. mock_exam_access.py).
VISUAL_DURATION_MINUTES = 240


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="guest-v1")


def dump_guest_cookie(participant_id: int, config_token: str) -> str:
    return _serializer().dumps({"participant_id": participant_id, "config_token": config_token})


def load_guest_cookie(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        payload = _serializer().loads(raw, max_age=COOKIE_MAX_AGE)
    except BadData:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def set_guest_cookie(response, participant_id: int, config_token: str) -> None:
    """Поставить подписанный cookie гостевой сессии.

    Живёт в сервисе, а не в роутере, потому что гостевую сессию открывают из
    двух мест: форма кода (app/api/guest_exam.py) и callback Telegram-входа
    (app/api/auth.py), а импорт роутера из роутера дал бы цикл."""
    response.set_cookie(
        key=GUEST_COOKIE_NAME,
        value=dump_guest_cookie(participant_id, config_token),
        httponly=True,
        samesite="lax",
        secure=True,
        max_age=COOKIE_MAX_AGE,
        path="/",
    )


def get_config_by_token(db: DBSession, token: str) -> GuestExamConfig | None:
    return db.query(GuestExamConfig).filter(GuestExamConfig.token == token).first()


def get_primary_config(db: DBSession) -> GuestExamConfig | None:
    """Ссылка, с которой сейчас работает админка (вкладки «Билеты»/«Работы»):
    активная и самая свежая, если активных нет — просто самая свежая."""
    active = (
        db.query(GuestExamConfig)
        .filter(GuestExamConfig.is_active == True)  # noqa: E712
        .order_by(GuestExamConfig.created_at.desc())
        .first()
    )
    if active:
        return active
    return db.query(GuestExamConfig).order_by(GuestExamConfig.created_at.desc()).first()


def list_participants_board(db: DBSession) -> list[dict]:
    """Участники со сданными/оценёнными работами — для вкладки «Работы»: один
    ряд на участника (имя + дата последней сдачи), с раскрытием по предметам
    (`subs["Рисунок"|"Композиция"]` → GuestSubmission, если сдан)."""
    rows = (
        db.query(GuestSubmission, GuestParticipant)
        .join(GuestParticipant, GuestSubmission.participant_id == GuestParticipant.id)
        .filter(GuestSubmission.status.in_(["submitted", "scored"]))
        .all()
    )
    board: dict[int, dict] = {}
    for submission, participant in rows:
        entry = board.setdefault(participant.id, {
            "participant": participant,
            "subs": {},
            "latest_at": None,
        })
        entry["subs"][submission.subject] = submission
        if submission.submitted_at and (
            entry["latest_at"] is None or submission.submitted_at > entry["latest_at"]
        ):
            entry["latest_at"] = submission.submitted_at

    board_list = list(board.values())
    board_list.sort(key=lambda e: e["latest_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return board_list


def list_all_participants(db: DBSession) -> list[dict]:
    """Все участники по всем ссылкам — для вкладки «Участники» суперадмина.

    В отличие от list_participants_board показывает и тех, кто только взял
    билет и не сдал: без этого тестовые записи невозможно найти и удалить.
    Вкладку «Работы» намеренно не расширяем — это очередь проверяющего, пустые
    ряды ей мешают (см. test_works_tab_hides_participant_without_submission)."""
    participants = (
        db.query(GuestParticipant)
        .order_by(GuestParticipant.created_at.desc())
        .all()
    )
    submissions = db.query(GuestSubmission).all()
    by_participant: dict[int, list[GuestSubmission]] = {}
    for submission in submissions:
        by_participant.setdefault(submission.participant_id, []).append(submission)

    rows = []
    for participant in participants:
        subs = by_participant.get(participant.id, [])
        rows.append({
            "participant": participant,
            "submissions": sorted(subs, key=lambda x: x.subject),
            "issued": sum(1 for x in subs if x.status == "issued"),
            "submitted": sum(1 for x in subs if x.status in ("submitted", "scored")),
        })
    return rows


def delete_participant(db: DBSession, participant_id: int) -> tuple[bool, list[str]]:
    """Удалить участника со всеми его сдачами. Возвращает (удалён, пути в S3).

    Пути отдаются наружу, чтобы чистку хранилища делал роутер — сервис не тянет
    зависимость от S3. В список попадают **только** файлы самого участника:
    его работа и фото обратной связи. Картинку билета трогать нельзя — она
    принадлежит общему ExamTicket и обслуживает всех, кому этот билет выпал
    и ещё выпадет."""
    participant = (
        db.query(GuestParticipant).filter(GuestParticipant.id == participant_id).first()
    )
    if not participant:
        return False, []

    submissions = (
        db.query(GuestSubmission)
        .filter(GuestSubmission.participant_id == participant_id)
        .all()
    )
    s3_paths = [
        path
        for submission in submissions
        for path in (submission.s3_path, submission.feedback_image_path)
        if path
    ]

    # Дети удаляются явно: каскад объявлен на уровне БД, но в тестах SQLite идёт
    # без PRAGMA foreign_keys, и там он бы не сработал.
    db.query(GuestVisit).filter(GuestVisit.participant_id == participant_id).update(
        {GuestVisit.participant_id: None}, synchronize_session=False
    )
    for submission in submissions:
        db.delete(submission)
    db.delete(participant)
    db.commit()
    return True, s3_paths


def record_visit(db: DBSession, config_id: int, participant_id: int | None = None) -> None:
    db.add(GuestVisit(config_id=config_id, participant_id=participant_id))
    db.commit()


def config_stats(db: DBSession, config_id: int) -> dict:
    visits = db.query(func.count(GuestVisit.id)).filter(GuestVisit.config_id == config_id).scalar() or 0
    participants = (
        db.query(func.count(GuestParticipant.id))
        .filter(GuestParticipant.config_id == config_id)
        .scalar() or 0
    )
    submitted = (
        db.query(func.count(GuestSubmission.id))
        .join(GuestParticipant, GuestSubmission.participant_id == GuestParticipant.id)
        .filter(GuestParticipant.config_id == config_id, GuestSubmission.status.in_(["submitted", "scored"]))
        .scalar() or 0
    )
    return {"visits": visits, "participants": participants, "submitted": submitted}


def get_participant(db: DBSession, participant_id: int | None, config_id: int) -> GuestParticipant | None:
    if not participant_id:
        return None
    return (
        db.query(GuestParticipant)
        .filter(GuestParticipant.id == participant_id, GuestParticipant.config_id == config_id)
        .first()
    )


def get_participant_by_code(db: DBSession, config_id: int, code: str) -> GuestParticipant | None:
    code_clean = (code or "").strip().upper()
    if not code_clean:
        return None
    return (
        db.query(GuestParticipant)
        .filter(
            GuestParticipant.config_id == config_id,
            GuestParticipant.participant_code == code_clean,
        )
        .first()
    )


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def create_participant(db: DBSession, config: GuestExamConfig, display_name: str) -> GuestParticipant:
    name_clean = (display_name or "").strip()[:200]
    if not name_clean:
        raise ValueError("empty_name")

    for _ in range(_CODE_GENERATION_ATTEMPTS):
        participant = GuestParticipant(
            config_id=config.id,
            display_name=name_clean,
            participant_code=_generate_code(),
        )
        db.add(participant)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(participant)
        return participant
    raise RuntimeError("Не удалось сгенерировать уникальный код участника")


def get_participant_by_telegram(
    db: DBSession, config_id: int, chat_id: int
) -> GuestParticipant | None:
    return (
        db.query(GuestParticipant)
        .filter(
            GuestParticipant.config_id == config_id,
            GuestParticipant.telegram_chat_id == chat_id,
        )
        .first()
    )


def telegram_display_name(first_name: str | None, last_name: str | None, username: str | None) -> str:
    """Имя участника из данных Telegram. Пустым не бывает: у аккаунта всегда
    есть хотя бы имя, но если Telegram его не отдал — подставляем username, а в
    последнюю очередь заглушку, потому что display_name NOT NULL."""
    parts = [p for p in (first_name, last_name) if p]
    name = " ".join(parts).strip()
    return (name or (username or "").strip() or "Участник")[:200]


def upsert_telegram_participant(
    db: DBSession,
    config: GuestExamConfig,
    *,
    chat_id: int,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
    existing: GuestParticipant | None = None,
) -> GuestParticipant:
    """Найти или завести участника по Telegram-аккаунту.

    `existing` — участник, узнанный по cookie в этом же браузере. Если он ещё не
    привязан к Telegram, chat_id дописывается ему, а не создаётся вторая запись:
    иначе тот, кто начал пробник до появления входа через Telegram, потерял бы
    уже взятый билет.
    """
    display_name = telegram_display_name(first_name, last_name, username)
    username_clean = (username or "").strip() or None

    participant = get_participant_by_telegram(db, config.id, chat_id)
    if not participant and existing is not None and existing.telegram_chat_id is None:
        participant = existing

    if participant:
        participant.telegram_chat_id = chat_id
        participant.telegram_username = username_clean
        participant.display_name = display_name
        participant.last_seen_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(participant)
        return participant

    for _ in range(_CODE_GENERATION_ATTEMPTS):
        participant = GuestParticipant(
            config_id=config.id,
            display_name=display_name,
            participant_code=_generate_code(),
            telegram_chat_id=chat_id,
            telegram_username=username_clean,
        )
        db.add(participant)
        try:
            db.commit()
        except IntegrityError:
            # Либо совпал сгенерированный код, либо параллельная вкладка успела
            # завести того же участника — во втором случае возвращаем его.
            db.rollback()
            existing_by_tg = get_participant_by_telegram(db, config.id, chat_id)
            if existing_by_tg:
                return existing_by_tg
            continue
        db.refresh(participant)
        return participant
    raise RuntimeError("Не удалось сгенерировать уникальный код участника")


def touch_participant(db: DBSession, participant: GuestParticipant) -> None:
    participant.last_seen_at = datetime.now(timezone.utc)
    db.commit()


def get_submission(db: DBSession, participant_id: int, subject: str) -> GuestSubmission | None:
    return (
        db.query(GuestSubmission)
        .filter(
            GuestSubmission.participant_id == participant_id,
            GuestSubmission.subject == subject,
        )
        .first()
    )


def get_pending_submission(
    db: DBSession, participant_id: int, exclude_subject: str | None = None
) -> GuestSubmission | None:
    """Взятый, но ещё не сданный билет участника (status="issued").

    Предметов всего два (MOCK_SUBJECTS), и пока такой билет висит, второй не
    выдаётся — см. issue_ticket и TicketLockedError."""
    query = db.query(GuestSubmission).filter(
        GuestSubmission.participant_id == participant_id,
        GuestSubmission.status == "issued",
    )
    if exclude_subject:
        query = query.filter(GuestSubmission.subject != exclude_subject)
    return query.first()


def _get_or_create_guest_assignment(db: DBSession, subject: str, created_by_id: int) -> ExamAssignment:
    """Один ExamAssignment(kind="guest") на предмет — билеты копятся в нём же,
    как обычные билеты внутри реального задания."""
    assignment = (
        db.query(ExamAssignment)
        .filter(ExamAssignment.kind == GUEST_ASSIGNMENT_KIND, ExamAssignment.subject == subject)
        .order_by(ExamAssignment.created_at.desc())
        .first()
    )
    if assignment:
        return assignment
    assignment = ExamAssignment(
        title=f"Гостевой режим — {subject}",
        subject=subject,
        kind=GUEST_ASSIGNMENT_KIND,
        created_by_id=created_by_id,
        status="published",
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def list_guest_tickets(db: DBSession) -> list[ExamTicket]:
    """Билеты гостевого режима по всем предметам — для вкладки «Билеты».

    ExamTicket не хранит subject сам (он на ExamAssignment) и не объявляет
    relationship — подставляем `.subject` на объект вручную, чтобы шаблон мог
    читать `ticket.subject` не завязываясь на join."""
    rows = (
        db.query(ExamTicket, ExamAssignment.subject)
        .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
        .filter(ExamAssignment.kind == GUEST_ASSIGNMENT_KIND)
        .order_by(ExamAssignment.subject, ExamTicket.created_at.desc())
        .all()
    )
    tickets = []
    for ticket, subject in rows:
        ticket.subject = subject
        tickets.append(ticket)
    return tickets


def create_guest_ticket(
    db: DBSession,
    *,
    subject: str,
    title: str,
    description: str | None,
    image_url: str | None,
    image_path: str | None,
    created_by_id: int,
) -> ExamTicket:
    assignment = _get_or_create_guest_assignment(db, subject, created_by_id)
    next_number = (
        db.query(func.max(ExamTicket.ticket_number))
        .filter(ExamTicket.assignment_id == assignment.id)
        .scalar() or 0
    ) + 1
    today = today_msk()
    ticket = ExamTicket(
        assignment_id=assignment.id,
        ticket_number=next_number,
        title=title,
        description=description,
        image_s3_url=image_url,
        image_s3_path=image_path,
        start_date=today,
        end_date=today + timedelta(days=_FAR_FUTURE_DAYS),
        opens_at=None,
        closes_at=None,
        assign_to_all=False,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def delete_guest_ticket(db: DBSession, ticket_id: int) -> bool:
    """Удаляет билет, только если он принадлежит гостевому (kind="guest") заданию —
    страховка от случайного удаления билета реального пробника по чужому id."""
    ticket = (
        db.query(ExamTicket)
        .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
        .filter(ExamTicket.id == ticket_id, ExamAssignment.kind == GUEST_ASSIGNMENT_KIND)
        .first()
    )
    if not ticket:
        return False
    db.delete(ticket)
    db.commit()
    return True


def _published_tickets_query(db: DBSession, subject: str):
    return (
        db.query(ExamTicket)
        .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
        .filter(
            ExamAssignment.kind == GUEST_ASSIGNMENT_KIND,
            ExamAssignment.subject == subject,
            ExamAssignment.status == "published",
        )
    )


def has_available_tickets(db: DBSession, subject: str) -> bool:
    """Есть ли хотя бы один билет по предмету — кнопка «Получить билет» на
    странице гостя активна только при True (см. app/api/guest_exam.py::guest_exam_page)."""
    return db.query(_published_tickets_query(db, subject).exists()).scalar()


def issue_ticket(db: DBSession, participant: GuestParticipant, subject: str) -> GuestSubmission:
    """Выдать билет по предмету — идемпотентно, один билет на предмет на участника.

    Поднимает TicketLockedError, если по другому предмету билет уже взят, а
    работа по нему ещё не загружена."""
    existing = get_submission(db, participant.id, subject)
    if existing:
        return existing

    # Гейт: пока по другому предмету билет взят и работа не загружена, второй
    # билет не выдаём. Проверка после идемпотентного возврата выше, иначе
    # повторный клик по уже выданному предмету блокировал бы сам себя.
    pending = get_pending_submission(db, participant.id, exclude_subject=subject)
    if pending:
        raise TicketLockedError(pending.subject)

    tickets = _published_tickets_query(db, subject).all()
    if not tickets:
        raise LookupError("no_active_tickets")
    ticket = random.choice(tickets)

    submission = GuestSubmission(
        participant_id=participant.id,
        subject=subject,
        ticket_id=ticket.id,
        ticket_title=ticket.title,
        ticket_description=ticket.description,
        ticket_image_url=ticket.image_s3_url,
        status="issued",
    )
    db.add(submission)
    try:
        db.commit()
    except IntegrityError:
        # Гонка (двойной клик/две вкладки) — билет по этому предмету уже выдан
        # параллельным запросом, возвращаем его вместо ошибки.
        db.rollback()
        existing = get_submission(db, participant.id, subject)
        if existing:
            return existing
        raise
    db.refresh(submission)
    return submission


def record_upload(
    db: DBSession, submission: GuestSubmission, s3_url: str | None, s3_path: str
) -> GuestSubmission:
    submission.s3_url = s3_url
    submission.s3_path = s3_path
    submission.submitted_at = datetime.now(timezone.utc)
    submission.status = "submitted"
    db.commit()
    db.refresh(submission)
    return submission


def cancel_upload(db: DBSession, submission: GuestSubmission) -> str | None:
    """Откатить загруженную работу к состоянию «билет выдан».

    Билет и его снимок (`ticket_*`) остаются за участником — он присылает работу
    по тому же заданию, заново билет брать не нужно. Возвращает путь файла в S3,
    чтобы чистку хранилища сделал роутер; сам файл участника (`s3_path`) удалять
    можно, картинка билета не трогается — она общая (см. delete_participant)."""
    s3_path = submission.s3_path
    submission.s3_url = None
    submission.s3_path = None
    submission.submitted_at = None
    submission.status = "issued"
    db.commit()
    db.refresh(submission)
    return s3_path


def score_submission(
    db: DBSession,
    submission: GuestSubmission,
    *,
    score,
    comment: str | None,
    scored_by_id: int,
    feedback_image_url: str | None = None,
    feedback_image_path: str | None = None,
) -> GuestSubmission:
    submission.score = score
    submission.comment = (comment or "").strip() or None
    submission.feedback_image_url = feedback_image_url or None
    submission.feedback_image_path = feedback_image_path or None
    submission.scored_by_id = scored_by_id
    submission.scored_at = datetime.now(timezone.utc)
    submission.status = "scored"
    db.commit()
    db.refresh(submission)
    return submission


# ── Статистика гостевой ссылки ───────────────────────────────────────────────
#
# Считаем в Python, а не в SQL: даты нужно резать по Москве (в контейнере UTC),
# а диалекты Postgres и SQLite делают это по-разному — тесты бы разошлись с
# продом. Модуль временный и объёмы маленькие (сотни строк на ссылку), так что
# цена выборки в память ничтожна по сравнению с риском разного поведения.

# Период по умолчанию — окно жизни самой ссылки: от первого дня, когда по ней
# был хоть один заход или участник, до сегодня. Пробник идёт коротким окном
# (26–28.08.2026 у ссылки «proba»), и «последние 30 дней» показывали бы три
# живых дня в стене нулей. Даты в запросе перекрывают это значение.
# См. plans/2026-08-27-apparchi-guest-to-student-carryover.md.
STATS_MAX_DAYS_RENDERED = 120


def _as_utc(value: datetime) -> datetime:
    """SQLite отдаёт наивные datetime, Postgres — aware. Приводим к UTC.
    Зеркалит app/dependencies.py::_as_utc, но сервис не тащит зависимость от
    слоя аутентификации ради двух строк."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _msk_date(value: datetime):
    return _as_utc(value).astimezone(MSK_TZ).date()


def _percent(part: int, whole: int) -> int | None:
    """Доля в процентах или None, если делить не на что. None означает
    «показывать нечего» — шаблон рисует прочерк, а не «0%», иначе пустая ссылка
    выглядит как ссылка с нулевой конверсией."""
    if not whole:
        return None
    return round(part * 100 / whole)


def config_activity_range(db: DBSession, config_id: int) -> tuple[date, date]:
    """Окно жизни ссылки: первый день с активностью → сегодня.

    Это период по умолчанию для вкладки «Статистика». У пробника окно короткое
    (26–28.08.2026), и фиксированные «последние 30 дней» утопили бы его в нулях.
    Если активности не было вовсе — отдаём сегодняшний день, чтобы период не
    оказался пустым.
    """
    today = today_msk()
    first_visit = (
        db.query(func.min(GuestVisit.created_at))
        .filter(GuestVisit.config_id == config_id).scalar()
    )
    first_participant = (
        db.query(func.min(GuestParticipant.created_at))
        .filter(GuestParticipant.config_id == config_id).scalar()
    )
    candidates = [_msk_date(v) for v in (first_visit, first_participant) if v]
    return (min(candidates) if candidates else today), today


def config_statistics(
    db: DBSession,
    config_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Полная статистика одной гостевой ссылки за период: воронка, разрез по
    предметам, динамика по дням. Границы включаются обе, даты московские.

    **Период режет людей по дню захода, а не работы по дню сдачи.** То есть это
    когорта: «из тех, кто зашёл в эти дни, столько-то дошло до оценки» — даже
    если оценку куратор поставил позже. Иначе на трёхдневном окне пробника
    воронка сама себе противоречила бы: человек попадал бы в «вошли», но
    выпадал из «сдали», потому что сдал в ночь на четвёртый день.

    Воронка считает **людей**, а не сдачи: «сдали работу» — это участники, у
    которых есть хоть одна сданная работа. Число сдач отдельно лежит в разрезе
    по предметам и в `submissions_total` — на вкладке «Ссылка» показано именно
    оно, поэтому цифры там и здесь по смыслу разные и подписаны по-разному.
    """
    if date_from is None or date_to is None:
        default_from, default_to = config_activity_range(db, config_id)
        date_from = date_from or default_from
        date_to = date_to or default_to
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    def _in_period(value) -> bool:
        return value is not None and date_from <= _msk_date(value) <= date_to

    visit_times = [
        created_at for (created_at,) in
        db.query(GuestVisit.created_at).filter(GuestVisit.config_id == config_id).all()
    ]
    visit_times = [t for t in visit_times if _in_period(t)]
    visits_total = len(visit_times)

    participants = [
        p for p in db.query(GuestParticipant)
        .filter(GuestParticipant.config_id == config_id).all()
        if _in_period(p.created_at)
    ]
    participant_ids = [p.id for p in participants]

    submissions = (
        db.query(GuestSubmission)
        .filter(GuestSubmission.participant_id.in_(participant_ids))
        .all()
        if participant_ids else []
    )

    by_participant: dict[int, list[GuestSubmission]] = {}
    for submission in submissions:
        by_participant.setdefault(submission.participant_id, []).append(submission)

    took_ticket = len(by_participant)
    handed_in = sum(
        1 for subs in by_participant.values()
        if any(s.status in ("submitted", "scored") for s in subs)
    )
    scored = sum(
        1 for subs in by_participant.values() if any(s.status == "scored" for s in subs)
    )

    funnel = [
        {"label": "Заходов на страницу", "value": visits_total, "percent": None},
        {"label": "Вошли и назвались", "value": len(participants),
         "percent": _percent(len(participants), visits_total)},
        {"label": "Взяли билет", "value": took_ticket,
         "percent": _percent(took_ticket, len(participants))},
        {"label": "Сдали работу", "value": handed_in, "percent": _percent(handed_in, took_ticket)},
        {"label": "Получили оценку", "value": scored, "percent": _percent(scored, handed_in)},
    ]

    # Разрез по предметам: здесь единица — сдача, а не человек.
    by_subject = []
    for subject in MOCK_SUBJECTS:
        subject_subs = [s for s in submissions if s.subject == subject]
        done = [s for s in subject_subs if s.status in ("submitted", "scored")]
        rated = [s for s in subject_subs if s.status == "scored" and s.score is not None]
        avg = (sum(float(s.score) for s in rated) / len(rated)) if rated else None
        by_subject.append({
            "subject": subject,
            "issued": len(subject_subs),
            "submitted": len(done),
            "scored": len(rated),
            "avg_score": round(avg, 1) if avg is not None else None,
        })

    pending_review = sum(1 for s in submissions if s.status == "submitted")

    # Динамика по дням — ровно выбранный период. Пустые дни внутри заполняем
    # нулями: без этого таблица «схлопывает» провалы и врёт про равномерный поток.
    total_days = (date_to - date_from).days + 1
    render_days = min(total_days, STATS_MAX_DAYS_RENDERED)
    render_from = date_to - timedelta(days=render_days - 1)
    buckets = {
        render_from + timedelta(days=offset): {"visits": 0, "joined": 0, "submitted": 0}
        for offset in range(render_days)
    }

    for created_at in visit_times:
        day = _msk_date(created_at)
        if day in buckets:
            buckets[day]["visits"] += 1

    for participant in participants:
        day = _msk_date(participant.created_at)
        if day in buckets:
            buckets[day]["joined"] += 1

    for submission in submissions:
        if not submission.submitted_at:
            continue
        day = _msk_date(submission.submitted_at)
        if day in buckets:
            buckets[day]["submitted"] += 1

    by_day = [
        {"date": day, **counts}
        for day, counts in sorted(buckets.items(), reverse=True)
    ]
    day_peak = max((d["visits"] for d in by_day), default=0)

    return {
        "visits": visits_total,
        "participants": len(participants),
        "submissions_total": sum(1 for s in submissions if s.status in ("submitted", "scored")),
        "pending_review": pending_review,
        "funnel": funnel,
        "by_subject": by_subject,
        "by_day": by_day,
        "day_peak": day_peak,
        "date_from": date_from,
        "date_to": date_to,
        "days": total_days,
        "days_shown": len(by_day),
        # Период длиннее лимита рисуем не целиком — числа выше при этом
        # посчитаны по всему периоду, обрезана только таблица дней.
        "days_truncated": render_days < total_days,
        "has_data": bool(visits_total or participants),
    }
