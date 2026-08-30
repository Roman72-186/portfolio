"""Учебные программы: календарь и сборка дня.

Экран Главного преподавателя (ранг роли >= 4). Месяц листается ссылкой
`?month=YYYY-MM`, сетка и отметки считаются на сервере: JS здесь только
раскрывает панели и шлёт сохранение.

Файл новый намеренно: `cabinet_admin.py` ведёт параллельная сессия, а
`cabinet_tracker_admin.py` уже большой и отвечает за разовые задачи.
"""

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf, require_csrf_header
from app.models.audit_log import AuditLog
from app.models.exam_assignment import ExamAssignment
from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, LearningTopic
from app.models.learning_video import LearningVideo
from app.models.mock_exam_quiz import MAX_QUIZ_QUESTIONS
from app.services.mock_exam_quiz import create_questions as create_mock_quiz_questions
from app.models.survey import QUESTION_TYPE_LABELS, QUESTION_TYPES
from app.services.video_catalog import publish_video
from app.models.tracker import (
    ITEM_CHECKLIST,
    ITEM_HOMEWORK,
    ITEM_KIND_LABELS,
    ITEM_LESSON,
    ITEM_MATERIAL,
    ITEM_MOCK_EXAM,
    ITEM_QUIZ,
    ITEM_SURVEY,
    ITEM_VIDEO,
    SOURCE_EXAM_ASSIGNMENT,
    SOURCE_HOMEWORK,
    SOURCE_LEARNING_TOPIC,
    SOURCE_SURVEY,
    TrackerTask,
)
from app.services import s3 as s3_service
from app.services.exam_tickets import (
    compose_assignment_title,
    create_ticket,
    default_schedule_for_day,
    ensure_mock_period_for,
    next_seq_number,
    parse_msk_datetime,
    validate_tags,
    validate_window,
)
from app.services.program import (
    WEEKDAY_LABELS,
    day_bounds,
    day_title_ru,
    ensure_item_topic,
    item_details,
    items_for_day,
    month_days,
    month_marks,
    msk_date,
    parse_day_iso,
    set_item_audience,
    shift_month,
    video_bindings,
    videos_for_picker,
)
from app.services.survey import (
    create_survey_with_questions,
    get_survey,
    list_surveys,
    question_counts as survey_question_counts,
)
from app.services.tracker import (
    create_homework,
    create_task,
    delete_task,
    get_homework,
    get_task,
    homework_images,
    resolve_assignees,
    set_homework_images,
    update_homework,
    update_task,
)
from app.services.tz import today_msk
from app.services.utils import compress_image
from app.services.video_topics import ambiguous_tag_names, count_topic_audience
from app.tmpl import templates

router = APIRouter(prefix="/cabinet/staff/program")

ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".gif", ".bmp",
    ".tif", ".tiff",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Оставлено локально для `program_month` (строит `month_title`) — `day_title_ru`
# в program.py закрывает формат заголовка дня, здесь другой формат ("Август 2026").
MONTH_NAMES = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


def _parse_month(raw: str | None, today: date) -> tuple[int, int]:
    """`?month=2026-09` → (2026, 9). Мусор и пустое значение — текущий месяц."""
    if not raw:
        return today.year, today.month
    try:
        year, month = raw.split("-", 1)
        year_num, month_num = int(year), int(month)
    except (ValueError, AttributeError):
        return today.year, today.month
    if not 1 <= month_num <= 12 or not 2000 <= year_num <= 2100:
        return today.year, today.month
    return year_num, month_num


def _parse_day(raw: str) -> date:
    day = parse_day_iso(raw)
    if day is None:
        raise HTTPException(status_code=404, detail="Такого дня нет")
    return day


def _edit_payloads(
    db: DBSession, items: list[TrackerTask], details: dict
) -> dict[int, dict]:
    """Данные для предзаполнения формы правки — по одному элементу на карточку.

    Только для видов из `editable_kinds`: пробник и анкета правкой пока не
    покрыты, им здесь делать нечего.
    """
    payloads: dict[int, dict] = {}
    for item in items:
        if item.kind not in (
            ITEM_VIDEO, ITEM_HOMEWORK, ITEM_MATERIAL, ITEM_QUIZ, ITEM_LESSON, ITEM_CHECKLIST,
        ):
            continue
        detail = details.get(item.id, {})
        payload: dict = {
            "title": item.title,
            "description": item.description or "",
            "subject": item.subject,
            "is_required": item.is_required,
        }
        if item.kind == ITEM_VIDEO and detail.get("video"):
            payload["catalog_video_id"] = detail["video"].id
        if item.kind == ITEM_HOMEWORK and detail.get("homework"):
            hw = detail["homework"]
            payload["submission_required"] = hw.submission_required
            payload["max_files"] = hw.max_files
            payload["images"] = [
                {"url": img.image_s3_url, "path": img.image_s3_path}
                for img in homework_images(db, hw.id)
            ]
        payloads[item.id] = payload
    return payloads


@router.get("", response_class=HTMLResponse)
def program_month(
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    month: str | None = None,
):
    today = today_msk()
    year, month_num = _parse_month(month, today)
    prev_year, prev_month = shift_month(year, month_num, -1)
    next_year, next_month = shift_month(year, month_num, 1)
    return templates.TemplateResponse(
        "cabinet_program.html",
        {
            "request": request,
            "user": user,
            "days": month_days(year, month_num, today),
            "marks": month_marks(db, year, month_num),
            "weekday_labels": WEEKDAY_LABELS,
            "kind_labels": ITEM_KIND_LABELS,
            "month_title": f"{MONTH_NAMES[month_num - 1].capitalize()} {year}",
            "prev_month": f"{prev_year}-{prev_month:02d}",
            "next_month": f"{next_year}-{next_month:02d}",
            "current_month": f"{today.year}-{today.month:02d}",
            "today_iso": today.isoformat(),
        },
    )


@router.get("/{iso}", response_class=HTMLResponse)
def program_day(
    iso: str,
    request: Request,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
):
    day = _parse_day(iso)
    today = today_msk()
    items = items_for_day(db, day)
    details = item_details(db, items)
    surveys = list_surveys(db)
    survey_counts = survey_question_counts(db, [s.id for s in surveys])
    return templates.TemplateResponse(
        "cabinet_program_day.html",
        {
            "request": request,
            "user": user,
            "day": day,
            "day_iso": day.isoformat(),
            "day_title": day_title_ru(day),
            # Прошлое только смотрим: элемент задним числом открылся бы ученикам
            # мгновенно, и «поставить на вчера» почти всегда опечатка.
            "is_past": day < today,
            # Правка закрывается строже создания (владелец 30.08.2026): как
            # только наступил день элемента, он уже мог уйти ученикам —
            # редактировать можно только строго будущее (`_guard_editable`).
            "can_edit_items": day > today,
            "month_href": f"/cabinet/staff/program?month={day.year}-{day.month:02d}",
            "items": items,
            "details": details,
            # Пробник и анкету включим сюда отдельным шагом, когда для них
            # появится реальная правка (владелец 30.08.2026: остальные виды —
            # строго будущее, эти два — следующая часть той же задачи).
            "editable_kinds": [
                ITEM_VIDEO, ITEM_HOMEWORK, ITEM_MATERIAL, ITEM_QUIZ, ITEM_LESSON, ITEM_CHECKLIST,
            ],
            "edit_payloads": _edit_payloads(db, items, details),
            "kind_labels": ITEM_KIND_LABELS,
            "subjects": MOCK_SUBJECTS,
            # Анкета — переиспользуемый шаблон (owner-решение 22–23.08): конструктор
            # предлагает готовые анкеты, чтобы не набирать один и тот же опрос
            # заново на каждой из восьми точек года.
            "surveys": [
                {"id": s.id, "title": s.title, "question_count": survey_counts.get(s.id, 0)}
                for s in surveys
            ],
            "question_types": [
                {"value": t, "label": QUESTION_TYPE_LABELS[t]} for t in QUESTION_TYPES
            ],
            # Ролики в календаре только выбираются: загрузка живёт на своей
            # вкладке, а один ролик занимает ровно один день (см. video_bindings).
            "catalog_videos": videos_for_picker(db),
        },
    )


class TicketPayload(BaseModel):
    """Окно билета (открывается/закрывается/минут на работу) сюда не входит —
    решение владельца 30.08.2026: убрать настройку периода и времени из
    конструктора, окно всегда берётся из `default_schedule_for_day(day)`
    (11:45–18:30 дня, 90 минут), день уже известен из `iso` в пути."""
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    image_url: str | None = Field(default=None, max_length=500)
    image_path: str | None = Field(default=None, max_length=300)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be empty")
        return value

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None


class SubjectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=200)
    tickets: list[TicketPayload] = Field(min_length=1, max_length=10)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        value = (value or "").strip()
        if value not in MOCK_SUBJECTS:
            raise ValueError("Unknown subject")
        return value


class AudiencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assign_to_all: bool = False
    tag_ids: list[int] = Field(default_factory=list, max_length=200)
    assignee_usernames: str = Field(default="", max_length=20_000)


class MockPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subjects: list[SubjectPayload] = Field(min_length=1, max_length=len(MOCK_SUBJECTS))
    audience: AudiencePayload = Field(default_factory=AudiencePayload)
    is_required: bool = True
    # Мини-опрос после сдачи (решение владельца 30.08.2026, та же конструкция,
    # что у видео) — один набор вопросов на все выбранные предметы сразу:
    # у Пробника нет экрана правки, поэтому не нужна id-сохраняющая развязка
    # video_quiz.py::sync_questions, только чистое создание при сохранении.
    quiz_questions: list[str] = Field(default_factory=list, max_length=MAX_QUIZ_QUESTIONS)

    @field_validator("quiz_questions")
    @classmethod
    def strip_quiz_questions(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


def _guard_future(day: date) -> None:
    if day < today_msk():
        raise HTTPException(
            status_code=422, detail="Прошедший день можно только смотреть"
        )


def _guard_editable(day: date) -> None:
    """Правка закрывается строже создания (владелец 30.08.2026): как только
    наступил день элемента, контент уже мог уйти ученикам — редактировать
    можно только то, что ещё строго в будущем."""
    if day <= today_msk():
        raise HTTPException(
            status_code=422, detail="Сегодняшний и прошедший день можно только смотреть"
        )


def _resolve_audience(db: DBSession, audience: AudiencePayload) -> tuple[list[int], list[int], list[str]]:
    tag_ids = [] if audience.assign_to_all else validate_tags(db, audience.tag_ids)
    assignee_ids, not_found = ([], [])
    if not audience.assign_to_all:
        assignee_ids, not_found = resolve_assignees(db, audience.assignee_usernames)
    if not audience.assign_to_all and not tag_ids and not assignee_ids:
        raise HTTPException(
            status_code=422,
            detail="Выберите, кому это доступно: теги, отдельные ученики или «всем»",
        )
    return tag_ids, assignee_ids, not_found


@router.post("/{iso}/mock", response_class=JSONResponse)
def create_mock_item(
    iso: str,
    payload: MockPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Пробник в дне: по одному заданию на предмет, билеты и адресация.

    Два предмета в один день — это два `ExamAssignment`: колонка `subject` у
    задания одна и NOT NULL, и разводить их иначе значило бы ломать выдачу
    билетов ученику, которая тоже идёт по предмету.
    """
    day = _parse_day(iso)
    _guard_future(day)
    tag_ids, assignee_ids, not_found = _resolve_audience(db, payload.audience)

    seen: set[str] = set()
    period_start: date | None = None
    period_end: date | None = None

    for block in payload.subjects:
        if block.subject in seen:
            raise HTTPException(
                status_code=422, detail=f"Предмет «{block.subject}» указан дважды"
            )
        seen.add(block.subject)

        note = (block.note or "").strip() or None
        seq = next_seq_number(db, "mock", block.subject)
        assignment = ExamAssignment(
            title=compose_assignment_title("mock", seq, block.subject, day, note),
            subject=block.subject,
            kind="mock",
            seq_number=seq,
            note=note,
            status="published",
            created_by_id=user["user_id"],
        )
        db.add(assignment)
        db.flush()

        if payload.quiz_questions:
            create_mock_quiz_questions(
                db, assignment_id=assignment.id, texts=payload.quiz_questions
            )

        # Окно билета больше не настраивается в конструкторе (решение
        # владельца 30.08.2026) — одно и то же для всех билетов дня.
        schedule = default_schedule_for_day(day)
        duration_minutes = schedule["duration_minutes"]
        restrict_start_by_duration = True

        latest_close: datetime | None = None
        for number, ticket in enumerate(block.tickets, start=1):
            opens_at = parse_msk_datetime(
                schedule["opens_at"], ticket_number=number, field_label="открывается"
            )
            closes_at = parse_msk_datetime(
                schedule["closes_at"], ticket_number=number, field_label="закрывается"
            )
            start_date, end_date = validate_window(
                ticket_number=number,
                opens_at=opens_at,
                closes_at=closes_at,
                duration_minutes=duration_minutes,
                restrict_start_by_duration=restrict_start_by_duration,
            )
            create_ticket(
                db,
                assignment,
                number=number,
                title=ticket.title,
                description=ticket.description,
                image_url=ticket.image_url,
                image_path=ticket.image_path,
                opens_at=opens_at,
                closes_at=closes_at,
                duration_minutes=duration_minutes,
                restrict_start_by_duration=restrict_start_by_duration,
                start_date=start_date,
                end_date=end_date,
                assign_to_all=payload.audience.assign_to_all,
                tag_ids=tag_ids,
                assignee_ids=assignee_ids,
            )
            latest_close = closes_at if latest_close is None else max(latest_close, closes_at)
            period_start = start_date if period_start is None else min(period_start, start_date)
            period_end = end_date if period_end is None else max(period_end, end_date)

        topic = ensure_item_topic(
            db, title=f"Пробник · {block.subject}", day=day, user_id=user["user_id"]
        )
        set_item_audience(
            db,
            topic,
            assign_to_all=payload.audience.assign_to_all,
            tag_ids=tag_ids,
            assignee_ids=assignee_ids,
        )
        task = create_task(
            db,
            title=f"Пробник по предмету «{block.subject}»",
            description=note,
            due_at=latest_close,
            subject=block.subject,
            topic_id=topic.id,
            kind=ITEM_MOCK_EXAM,
            source_kind=SOURCE_EXAM_ASSIGNMENT,
            source_id=assignment.id,
            user_id=user["user_id"],
            is_required=payload.is_required,
        )
        task.is_published = True
        db.add(
            AuditLog(
                action="program_mock_create",
                performed_by_id=user["user_id"],
                details=json.dumps(
                    {
                        "day": iso,
                        "subject": block.subject,
                        "assignment_id": assignment.id,
                        "tickets": len(block.tickets),
                    },
                    ensure_ascii=False,
                ),
            )
        )

    if period_start and period_end:
        ensure_mock_period_for(
            db, start_date=period_start, end_date=period_end, user_id=user["user_id"]
        )
    db.commit()
    return JSONResponse(
        {
            "ok": True,
            "not_found": not_found,
            "audience_size": count_topic_audience(
                db,
                assign_to_all=payload.audience.assign_to_all,
                tag_ids=tag_ids,
                assignee_ids=assignee_ids,
            ),
            "ambiguous_tags": ambiguous_tag_names(db, tag_ids),
        }
    )


class VideoPayload(BaseModel):
    """Постановка ролика в день. Сам ролик уже лежит в каталоге.

    Название, описание и обложка — необязательные: календарь их больше не
    показывает и не присылает, они берутся у выбранного ролика. Поля оставлены
    ради прежних вызовов, и присланное значение по-прежнему переписывает
    карточку ролика в каталоге.
    """

    model_config = ConfigDict(extra="forbid")

    catalog_video_id: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    cover_url: str | None = Field(default=None, max_length=500)
    cover_path: str | None = Field(default=None, max_length=300)
    subject: str | None = Field(default=None, max_length=50)
    is_required: bool = True
    audience: AudiencePayload = Field(default_factory=AudiencePayload)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if value not in MOCK_SUBJECTS:
            raise ValueError("Unknown subject")
        return value


class HomeworkItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    subject: str | None = Field(default=None, max_length=50)
    submission_required: bool = True
    max_files: int = Field(default=1, ge=0, le=20)
    images: list[dict] = Field(default_factory=list, max_length=20)
    is_required: bool = True
    audience: AudiencePayload = Field(default_factory=AudiencePayload)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be empty")
        return value

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if value not in MOCK_SUBJECTS:
            raise ValueError("Unknown subject")
        return value


class SimpleItemPayload(BaseModel):
    """Общая форма для «Материалов», «Теста по теории», «Занятия» и
    «Чек-листа и проверок» — у них нет своей сущности (в отличие от видео,
    домашки, пробника, анкеты), поэтому одни и те же поля на все четыре.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    subject: str | None = Field(default=None, max_length=50)
    is_required: bool = True
    audience: AudiencePayload = Field(default_factory=AudiencePayload)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be empty")
        return value

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if value not in MOCK_SUBJECTS:
            raise ValueError("Unknown subject")
        return value


class SurveyOptionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=300)
    is_correct: bool = False


class SurveyQuestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500)
    question_type: str = Field(default="text")
    options: list[SurveyOptionPayload] = Field(default_factory=list, max_length=20)

    @field_validator("question_type")
    @classmethod
    def validate_question_type(cls, value: str) -> str:
        if value not in QUESTION_TYPES:
            raise ValueError("Unknown question type")
        return value


class SurveyItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Готовый шаблон вместо новой анкеты: survey_id задан — title/questions
    # игнорируются, вопросы не копируются и не трогаются (owner-решение,
    # анкета переиспользуется на восьми точках года).
    survey_id: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=200)
    questions: list[SurveyQuestionPayload] = Field(default_factory=list, max_length=50)
    is_required: bool = True
    audience: AudiencePayload = Field(default_factory=AudiencePayload)


def _audience_reply(db: DBSession, audience: AudiencePayload, tag_ids, not_found) -> dict:
    return {
        "ok": True,
        "not_found": not_found,
        "audience_size": count_topic_audience(
            db,
            assign_to_all=audience.assign_to_all,
            tag_ids=tag_ids,
            assignee_ids=[],
        ),
        "ambiguous_tags": ambiguous_tag_names(db, tag_ids),
    }


@router.post("/upload-cover")
async def upload_cover(
    user: Annotated[dict, Depends(require_admin_role)],
    _csrf: Annotated[None, Depends(require_csrf)],
    file: UploadFile = File(...),
):
    """Обложка урока. Своя картинка в S3: thumbnail у Bunny не реализован."""
    content_type = (file.content_type or "").lower()
    filename = file.filename or "cover.jpg"
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if not content_type.startswith("image/") and ext not in ALLOWED_IMAGE_EXTENSIONS:
        return JSONResponse(
            {"ok": False, "error": "Файл не является изображением"}, status_code=422
        )
    data = await file.read()
    if not data:
        return JSONResponse({"ok": False, "error": "Пустой файл"}, status_code=422)
    if len(data) > MAX_IMAGE_BYTES:
        return JSONResponse(
            {"ok": False, "error": "Файл слишком большой (макс. 10 МБ)"}, status_code=413
        )

    s3_path = f"Обложки видео/{uuid.uuid4().hex[:12]}.jpg"
    url = s3_service.upload_to_s3(s3_path, compress_image(data), "image/jpeg")
    if s3_service.is_configured() and not url:
        return JSONResponse(
            {"ok": False, "error": "Ошибка загрузки в хранилище"}, status_code=502
        )
    return JSONResponse({"ok": True, "url": url, "path": s3_path if url else None})


@router.post("/{iso}/video", response_class=JSONResponse)
def create_video_item(
    iso: str,
    payload: VideoPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Видеоматериал в дне: ролик выбран из каталога, здесь его привязка.

    Файл заливают на вкладке «Загрузка видео», строку каталога создаёт
    `/cabinet/admin/videos/create-upload`. Нам остаётся завести служебную тему с
    аудиторией и поставить элемент в день.
    """
    day = _parse_day(iso)
    _guard_future(day)
    tag_ids, assignee_ids, not_found = _resolve_audience(db, payload.audience)

    video = db.get(LearningVideo, payload.catalog_video_id)
    if video is None or video.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Ролик не найден")

    # Один ролик — один день: привязка лежит в единственной колонке
    # `topic_id`, и постановка занятого ролика отобрала бы его у прежнего дня
    # вместе с автозакрытием задачи в трекере.
    binding = video_bindings(db).get(video.id)
    if binding and binding["kind"] == TOPIC_KIND_PROGRAM_ITEM and binding["day"]:
        raise HTTPException(status_code=422, detail=binding["label"])

    title = payload.title or video.title
    topic = ensure_item_topic(
        db, title=f"Видео · {title}", day=day, user_id=user["user_id"]
    )
    set_item_audience(
        db,
        topic,
        assign_to_all=payload.audience.assign_to_all,
        tag_ids=tag_ids,
        assignee_ids=assignee_ids,
    )
    video.topic_id = topic.id
    # Постановка в день — это и есть решение куратора «показать ученикам»:
    # второго отдельного клика «Опубликовать» требовать не нужно (владелец
    # 29.08.2026, живой баг — куратор поставил ролик в день, обработка на Bunny
    # ещё не закончилась, и видео зависло неопубликованным без отдельного
    # напоминания вернуться и нажать кнопку на другой странице). Если ролик уже
    # готов — публикуем сразу; если ещё обрабатывается — `auto_publish_on_ready`
    # заставит фоновую проверку (exam_scheduler.py::_run_video_status_sync)
    # опубликовать его самостоятельно, как только Bunny закончит.
    if video.status == "ready":
        publish_video(video, user_id=user["user_id"])
    else:
        video.auto_publish_on_ready = True
    # Карточку ролика переписываем только тем, что реально прислали: календарь
    # берёт название и обложку из каталога и ничего о них не сообщает. Ключ
    # `description` в теле — а не просто «не None» — иначе прислать пустую
    # строку, чтобы стереть описание, было бы невозможно (`""` после
    # `strip_description` тоже превращается в `None`, как и «не прислали»).
    description_provided = "description" in payload.model_fields_set
    if payload.title:
        video.title = payload.title
    if description_provided:
        video.description = payload.description
    if payload.cover_url is not None:
        video.cover_s3_url = payload.cover_url
        video.cover_s3_path = payload.cover_path

    task = create_task(
        db,
        title=title,
        description=payload.description if description_provided else video.description,
        due_at=day_bounds(day)[0],
        subject=payload.subject,
        topic_id=topic.id,
        kind=ITEM_VIDEO,
        source_kind=SOURCE_LEARNING_TOPIC,
        source_id=topic.id,
        is_required=payload.is_required,
        user_id=user["user_id"],
    )
    task.is_published = True
    db.add(
        AuditLog(
            action="program_video_create",
            performed_by_id=user["user_id"],
            details=json.dumps(
                {"day": iso, "video_id": video.id, "topic_id": topic.id},
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    return JSONResponse(_audience_reply(db, payload.audience, tag_ids, not_found))


@router.post("/{iso}/homework", response_class=JSONResponse)
def create_homework_item(
    iso: str,
    payload: HomeworkItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Самостоятельная работа: та же сущность домашки, что и в трекере."""
    day = _parse_day(iso)
    _guard_future(day)
    tag_ids, assignee_ids, not_found = _resolve_audience(db, payload.audience)

    topic = ensure_item_topic(
        db, title=f"Самостоятельная · {payload.title}", day=day, user_id=user["user_id"]
    )
    set_item_audience(
        db,
        topic,
        assign_to_all=payload.audience.assign_to_all,
        tag_ids=tag_ids,
        assignee_ids=assignee_ids,
    )
    homework = create_homework(
        db,
        title=payload.title,
        description=payload.description,
        subject=payload.subject,
        submission_required=payload.submission_required,
        max_files=payload.max_files,
        user_id=user["user_id"],
    )
    set_homework_images(db, homework, payload.images)

    task = create_task(
        db,
        title=payload.title,
        description=payload.description,
        # Конец дня минус минута: верхняя граница суток принадлежит уже
        # следующему дню, и элемент уехал бы в соседнюю клетку календаря.
        due_at=day_bounds(day)[1] - timedelta(minutes=1),
        subject=payload.subject,
        topic_id=topic.id,
        kind=ITEM_HOMEWORK,
        source_kind=SOURCE_HOMEWORK,
        source_id=homework.id,
        is_required=payload.is_required,
        user_id=user["user_id"],
    )
    task.is_published = True
    db.add(
        AuditLog(
            action="program_homework_create",
            performed_by_id=user["user_id"],
            details=json.dumps(
                {"day": iso, "homework_id": homework.id}, ensure_ascii=False
            ),
        )
    )
    db.commit()
    return JSONResponse(_audience_reply(db, payload.audience, tag_ids, not_found))


_SIMPLE_ITEM_TOPIC_PREFIX = {
    ITEM_MATERIAL: "Материал",
    ITEM_QUIZ: "Тест по теории",
    ITEM_LESSON: "Занятие",
    ITEM_CHECKLIST: "Чек-лист",
}


def _create_simple_item(
    iso: str,
    kind: str,
    payload: SimpleItemPayload,
    user: dict,
    db: DBSession,
) -> JSONResponse:
    """Материал, тест по теории, занятие, чек-лист — простые элементы без
    своей сущности: только заголовок, описание и предмет. `source_kind` не
    заводим, поэтому `task_action.html` рисует обычную галочку «Отметить»,
    ученик закрывает такой элемент сам.
    """
    day = _parse_day(iso)
    _guard_future(day)
    tag_ids, assignee_ids, not_found = _resolve_audience(db, payload.audience)

    prefix = _SIMPLE_ITEM_TOPIC_PREFIX[kind]
    topic = ensure_item_topic(
        db, title=f"{prefix} · {payload.title}", day=day, user_id=user["user_id"]
    )
    set_item_audience(
        db,
        topic,
        assign_to_all=payload.audience.assign_to_all,
        tag_ids=tag_ids,
        assignee_ids=assignee_ids,
    )
    task = create_task(
        db,
        title=payload.title,
        description=payload.description,
        # Конец дня минус минута — тот же приём, что у самостоятельной работы и
        # анкеты: верхняя граница суток принадлежит уже следующему дню.
        due_at=day_bounds(day)[1] - timedelta(minutes=1),
        subject=payload.subject,
        topic_id=topic.id,
        kind=kind,
        is_required=payload.is_required,
        user_id=user["user_id"],
    )
    task.is_published = True
    db.add(
        AuditLog(
            action=f"program_{kind}_create",
            performed_by_id=user["user_id"],
            details=json.dumps({"day": iso, "topic_id": topic.id}, ensure_ascii=False),
        )
    )
    db.commit()
    return JSONResponse(_audience_reply(db, payload.audience, tag_ids, not_found))


@router.post("/{iso}/material", response_class=JSONResponse)
def create_material_item(
    iso: str,
    payload: SimpleItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    return _create_simple_item(iso, ITEM_MATERIAL, payload, user, db)


@router.post("/{iso}/quiz", response_class=JSONResponse)
def create_quiz_item(
    iso: str,
    payload: SimpleItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Тест по теории — отдельная вкладка недели, не путать с мини-опросом
    из трёх вопросов после видео (`app/models/video_quiz.py`)."""
    return _create_simple_item(iso, ITEM_QUIZ, payload, user, db)


@router.post("/{iso}/lesson", response_class=JSONResponse)
def create_lesson_item(
    iso: str,
    payload: SimpleItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Занятие или эфир. Ссылка на созвон как отдельная кнопка — не сделано
    здесь (нужна колонка и решение владельца), пока в описании текстом."""
    return _create_simple_item(iso, ITEM_LESSON, payload, user, db)


@router.post("/{iso}/checklist", response_class=JSONResponse)
def create_checklist_item(
    iso: str,
    payload: SimpleItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    return _create_simple_item(iso, ITEM_CHECKLIST, payload, user, db)


@router.post("/{iso}/survey", response_class=JSONResponse)
def create_survey_item(
    iso: str,
    payload: SurveyItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Анкета в дне: готовый шаблон из базы или новая, собранная на месте.

    `survey_id` задан — переиспользуем анкету как есть (owner-решение:
    анкета показывается несколько раз за год, вопросы не копируются). Иначе
    заводим новую анкету с вопросами тем же конструктором.
    """
    day = _parse_day(iso)
    _guard_future(day)
    tag_ids, assignee_ids, not_found = _resolve_audience(db, payload.audience)

    if payload.survey_id is not None:
        survey = get_survey(db, payload.survey_id)
        if survey is None:
            raise HTTPException(status_code=404, detail="Анкета не найдена")
    else:
        title = (payload.title or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="Укажите название анкеты")
        if not payload.questions:
            raise HTTPException(status_code=422, detail="Добавьте хотя бы один вопрос")
        survey = create_survey_with_questions(
            db,
            title=title,
            questions=[q.model_dump() for q in payload.questions],
            user_id=user["user_id"],
        )

    topic = ensure_item_topic(
        db, title=f"Анкета · {survey.title}", day=day, user_id=user["user_id"]
    )
    set_item_audience(
        db,
        topic,
        assign_to_all=payload.audience.assign_to_all,
        tag_ids=tag_ids,
        assignee_ids=assignee_ids,
    )
    task = create_task(
        db,
        title=survey.title,
        # Конец дня минус минута — тот же приём, что у самостоятельной работы:
        # верхняя граница суток принадлежит уже следующему дню.
        due_at=day_bounds(day)[1] - timedelta(minutes=1),
        topic_id=topic.id,
        kind=ITEM_SURVEY,
        source_kind=SOURCE_SURVEY,
        source_id=survey.id,
        is_required=payload.is_required,
        user_id=user["user_id"],
    )
    task.is_published = True
    db.add(
        AuditLog(
            action="program_survey_create",
            performed_by_id=user["user_id"],
            details=json.dumps(
                {
                    "day": iso,
                    "survey_id": survey.id,
                    "reused": payload.survey_id is not None,
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    return JSONResponse(_audience_reply(db, payload.audience, tag_ids, not_found))


def _get_editable_task(db: DBSession, task_id: int, kind: str) -> TrackerTask:
    """Найти элемент дня для правки: тот же вид, ещё не прошедший день.

    День берём из `task.due_at`, а не из URL — правка идёт по id элемента,
    и клиентский `iso` здесь не участвует и подделать его нельзя.
    """
    task = get_task(db, task_id)
    if task is None or task.kind != kind:
        raise HTTPException(status_code=404, detail="Элемент не найден")
    _guard_editable(msk_date(task.due_at))
    return task


def _update_simple_item(
    task_id: int,
    kind: str,
    payload: SimpleItemPayload,
    user: dict,
    db: DBSession,
) -> JSONResponse:
    task = _get_editable_task(db, task_id, kind)

    if task.topic_id:
        topic = db.get(LearningTopic, task.topic_id)
        if topic is not None:
            topic.title = f"{_SIMPLE_ITEM_TOPIC_PREFIX[kind]} · {payload.title}"[:200]

    update_task(
        task,
        title=payload.title,
        description=payload.description,
        due_at=task.due_at,
        subject=payload.subject,
        assign_to_all=task.assign_to_all,
        kind=kind,
        is_required=payload.is_required,
    )
    db.add(
        AuditLog(
            action=f"program_{kind}_update",
            performed_by_id=user["user_id"],
            details=json.dumps({"task_id": task_id}, ensure_ascii=False),
        )
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/items/{task_id}/material", response_class=JSONResponse)
def update_material_item(
    task_id: int,
    payload: SimpleItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    return _update_simple_item(task_id, ITEM_MATERIAL, payload, user, db)


@router.post("/items/{task_id}/quiz", response_class=JSONResponse)
def update_quiz_item(
    task_id: int,
    payload: SimpleItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    return _update_simple_item(task_id, ITEM_QUIZ, payload, user, db)


@router.post("/items/{task_id}/lesson", response_class=JSONResponse)
def update_lesson_item(
    task_id: int,
    payload: SimpleItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    return _update_simple_item(task_id, ITEM_LESSON, payload, user, db)


@router.post("/items/{task_id}/checklist", response_class=JSONResponse)
def update_checklist_item(
    task_id: int,
    payload: SimpleItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    return _update_simple_item(task_id, ITEM_CHECKLIST, payload, user, db)


@router.post("/items/{task_id}/homework", response_class=JSONResponse)
def update_homework_item(
    task_id: int,
    payload: HomeworkItemPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task = _get_editable_task(db, task_id, ITEM_HOMEWORK)
    homework = get_homework(db, task.source_id) if task.source_id else None
    if homework is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")

    update_homework(
        homework,
        title=payload.title,
        description=payload.description,
        subject=payload.subject,
        submission_required=payload.submission_required,
        max_files=payload.max_files,
    )
    set_homework_images(db, homework, payload.images)

    if task.topic_id:
        topic = db.get(LearningTopic, task.topic_id)
        if topic is not None:
            topic.title = f"Самостоятельная · {payload.title}"[:200]

    update_task(
        task,
        title=payload.title,
        description=payload.description,
        due_at=task.due_at,
        subject=payload.subject,
        assign_to_all=task.assign_to_all,
        kind=ITEM_HOMEWORK,
        is_required=payload.is_required,
    )
    db.add(
        AuditLog(
            action="program_homework_update",
            performed_by_id=user["user_id"],
            details=json.dumps(
                {"task_id": task_id, "homework_id": homework.id}, ensure_ascii=False
            ),
        )
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/items/{task_id}/video", response_class=JSONResponse)
def update_video_item(
    task_id: int,
    payload: VideoPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task = _get_editable_task(db, task_id, ITEM_VIDEO)

    new_video = db.get(LearningVideo, payload.catalog_video_id)
    if new_video is None or new_video.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Ролик не найден")

    old_video = (
        db.query(LearningVideo)
        .filter(
            LearningVideo.topic_id == task.topic_id,
            LearningVideo.deleted_at.is_(None),
        )
        .first()
        if task.topic_id
        else None
    )

    # Ролик меняем, только если реально выбрали другой — свой же ролик не
    # конфликт с самим собой, а `video_bindings` без исключения принял бы
    # его за занятый этим же днём.
    if old_video is None or new_video.id != old_video.id:
        binding = video_bindings(db).get(new_video.id)
        if binding and binding["kind"] == TOPIC_KIND_PROGRAM_ITEM and binding["day"]:
            raise HTTPException(status_code=422, detail=binding["label"])
        if old_video is not None:
            old_video.topic_id = None
        new_video.topic_id = task.topic_id
        if new_video.status == "ready":
            publish_video(new_video, user_id=user["user_id"])
        else:
            new_video.auto_publish_on_ready = True

    title = payload.title or new_video.title
    # Ключ `description` в теле, а не просто «не None» — иначе прислать
    # пустую строку, чтобы стереть описание, было бы невозможно (`""` после
    # `strip_description` тоже превращается в `None`, как и «не прислали»).
    description_provided = "description" in payload.model_fields_set
    if payload.title:
        new_video.title = payload.title
    if description_provided:
        new_video.description = payload.description
    if payload.cover_url is not None:
        new_video.cover_s3_url = payload.cover_url
        new_video.cover_s3_path = payload.cover_path

    if task.topic_id:
        topic = db.get(LearningTopic, task.topic_id)
        if topic is not None:
            topic.title = f"Видео · {title}"[:200]

    update_task(
        task,
        title=title,
        description=payload.description if description_provided else new_video.description,
        due_at=task.due_at,
        subject=payload.subject,
        assign_to_all=task.assign_to_all,
        kind=ITEM_VIDEO,
        is_required=payload.is_required,
    )
    db.add(
        AuditLog(
            action="program_video_update",
            performed_by_id=user["user_id"],
            details=json.dumps(
                {"task_id": task_id, "video_id": new_video.id}, ensure_ascii=False
            ),
        )
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/items/{task_id}/delete", response_class=JSONResponse)
def delete_program_item(
    task_id: int,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task = get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Элемент не найден")
    delete_task(task)
    db.commit()
    return JSONResponse({"ok": True})
