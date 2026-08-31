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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS, TARIFFS
from app.db.database import get_db
from app.dependencies import require_admin_role, require_csrf, require_csrf_header
from app.models.audit_log import AuditLog
from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, LearningTopic
from app.models.learning_video import LearningVideo
from app.models.task_block import (
    BLOCK_QUESTION,
    BLOCK_TYPE_LABELS,
    BLOCK_TYPES,
    MAX_BLOCK_IMAGES,
    MAX_BLOCKS,
    QUESTION_TEXT,
)
from app.services.task_blocks import (
    get_blocks as get_task_blocks,
    get_images as get_task_block_images,
    get_options as get_task_block_options,
    sync_blocks as sync_task_blocks,
)
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
    get_ticket_tariffs,
    next_seq_number,
    parse_msk_datetime,
    set_ticket_tariffs,
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
    get_questions as get_survey_questions,
    has_responses as survey_has_responses,
    list_surveys,
    options_by_question as survey_options_by_question,
    question_counts as survey_question_counts,
    set_questions as set_survey_questions,
    update_survey_title,
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
from app.services.video_topics import (
    ambiguous_tag_names,
    count_topic_audience,
    get_assignee_ids,
    get_tag_ids,
    get_topic_tariffs,
    set_topic_tariffs,
)
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

    Анкета — не по виду `editable_kinds`, а по месту: строку добавляем,
    только если этот переиспользуемый шаблон сейчас стоит ровно в этом одном
    дне (см. `_survey_usage_count` — та же проверка стоит и на самом
    эндпоинте правки, здесь только чтобы не рисовать кнопку впустую).
    """
    payloads: dict[int, dict] = {}
    for item in items:
        if item.kind not in (
            ITEM_VIDEO, ITEM_HOMEWORK, ITEM_MATERIAL, ITEM_QUIZ, ITEM_LESSON, ITEM_CHECKLIST,
            ITEM_MOCK_EXAM, ITEM_SURVEY,
        ):
            continue
        if item.kind == ITEM_SURVEY and (
            not item.source_id or _survey_usage_count(db, item.source_id) > 1
        ):
            continue
        detail = details.get(item.id, {})
        payload: dict = {
            "title": item.title,
            "description": item.description or "",
            "subject": item.subject,
            "is_required": item.is_required,
        }
        # Тариф правится, только пока тема элемента — служебная тема ровно
        # этого элемента (TOPIC_KIND_PROGRAM_ITEM). Элементы, попавшие в день
        # через copy_week, делят тему на всю неделю — там чек-бокс тарифа не
        # рисуется вовсе (см. _apply_element_tariff).
        topic = db.get(LearningTopic, item.topic_id) if item.topic_id else None
        if topic is not None and topic.kind == TOPIC_KIND_PROGRAM_ITEM:
            payload["tariff_restricted"] = topic.tariff_restricted
            payload["tariffs"] = get_topic_tariffs(db, topic.id)
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
        if item.kind == ITEM_MOCK_EXAM and item.source_id:
            payload["tickets"] = [
                {
                    "id": t.id, "title": t.title, "description": t.description or "",
                    "image_url": t.image_s3_url, "image_path": t.image_s3_path,
                }
                for t in db.query(ExamTicket)
                .filter(ExamTicket.assignment_id == item.source_id)
                .order_by(ExamTicket.ticket_number)
                .all()
            ]
        if item.kind == ITEM_SURVEY and item.source_id:
            survey = get_survey(db, item.source_id)
            payload["title"] = survey.title if survey else ""
            questions = get_survey_questions(db, item.source_id)
            options = survey_options_by_question(db, [q.id for q in questions])
            payload["questions"] = [
                {
                    "id": q.id, "text": q.text, "question_type": q.question_type,
                    "options": [
                        {"id": o.id, "text": o.text, "is_correct": o.is_correct}
                        for o in options.get(q.id, [])
                    ],
                }
                for q in questions
            ]
            payload["survey_locked"] = survey_has_responses(db, item.source_id)
        # Блоки конструктора — у всех видов элемента без исключения, включая
        # видеоматериал: в этом и смысл универсального конструктора. Не путать
        # с мини-опросом самого ролика (`video_quiz`, экран «Загрузка видео») —
        # тот привязан к `LearningVideo` и правится только там, чтобы не было
        # двух мест редактирования одного и того же.
        blocks = get_task_blocks(db, item.id)
        block_options = get_task_block_options(db, [b.id for b in blocks])
        block_images = get_task_block_images(db, [b.id for b in blocks])
        payload["blocks"] = [
            {
                "id": b.id,
                "block_type": b.block_type,
                "title": b.title,
                "body": b.body,
                "video_id": b.video_id,
                "images": [
                    {"url": i.image_s3_url, "path": i.image_s3_path}
                    for i in block_images.get(b.id, [])
                ],
                "url": b.url,
                "question_type": b.question_type,
                "hidden_until_done": b.hidden_until_done,
                "options": [
                    {"id": o.id, "text": o.text, "is_correct": o.is_correct}
                    for o in block_options.get(b.id, [])
                ]
                if b.block_type == BLOCK_QUESTION
                else [],
            }
            for b in blocks
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
            # Анкета в списке видов, но кнопка «Изменить» рисуется только
            # если для неё есть запись в edit_payloads — она условная (см.
            # _edit_payloads: только пока шаблон стоит ровно в одном дне).
            "editable_kinds": [
                ITEM_VIDEO, ITEM_HOMEWORK, ITEM_MATERIAL, ITEM_QUIZ, ITEM_LESSON, ITEM_CHECKLIST,
                ITEM_MOCK_EXAM, ITEM_SURVEY,
            ],
            "edit_payloads": _edit_payloads(db, items, details),
            "kind_labels": ITEM_KIND_LABELS,
            "subjects": MOCK_SUBJECTS,
            "tariffs": TARIFFS,
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
            # вкладке. Занятость больше не блокирует выбор — один ролик можно
            # поставить блоком в несколько заданий (владелец 31.08.2026).
            "catalog_videos": videos_for_picker(db),
            # Кнопки «что добавить» в редакторе блоков. Порядок здесь и есть
            # порядок кнопок в форме.
            "block_types": [(t, BLOCK_TYPE_LABELS[t]) for t in BLOCK_TYPES],
            "max_block_images": MAX_BLOCK_IMAGES,
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


class BlockImageItem(BaseModel):
    """Одна картинка блока-галереи. Файл уже лежит в S3: форма шлёт только
    ссылку и путь, как это делают обложки видео и картинки самостоятельной."""

    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=500)
    path: str | None = Field(default=None, max_length=300)


class BlockOptionItem(BaseModel):
    """Вариант ответа у блока-вопроса. `id` — существующий вариант (правится
    на месте, выбор учеников сохраняется), `None` — новый."""

    model_config = ConfigDict(extra="forbid")
    id: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1, max_length=300)
    is_correct: bool = False

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Option text cannot be empty")
        return value


class BlockItem(BaseModel):
    """Один блок содержимого элемента (владелец 31.08.2026, универсальный
    конструктор — см. `app/models/task_block.py`).

    Один класс на все пять типов: специализированные поля не обязательны и
    заполняются только под свой тип, лишние сервис вычищает сам
    (`task_blocks.sync_blocks`). `id` — существующий блок, правится на месте
    вместе с уже сохранёнными ответами учеников; `None` — новый.

    Сюда переехал прежний мини-опрос: блок с `block_type="question"` и
    `question_type="text"` — это ровно то, чем был `QuizQuestionItem`.
    """

    model_config = ConfigDict(extra="forbid")
    id: int | None = Field(default=None, ge=1)
    block_type: str = Field(min_length=1, max_length=20)
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=5000)
    video_id: int | None = Field(default=None, ge=1)
    images: list[BlockImageItem] = Field(
        default_factory=list, max_length=MAX_BLOCK_IMAGES
    )
    url: str | None = Field(default=None, max_length=500)
    question_type: str | None = Field(default=None, max_length=20)
    options: list[BlockOptionItem] = Field(default_factory=list, max_length=20)
    # Вопрос-рефлексия: показывается только после того, как ученик закрыл
    # задание. В проверку «ответил ли на всё» не входит — иначе задание нельзя
    # было бы закрыть никогда (развязка согласована владельцем 31.08.2026).
    hidden_until_done: bool = False

    @model_validator(mode="after")
    def choice_question_needs_a_right_answer(self) -> "BlockItem":
        """Вопрос с вариантами нельзя сохранить, не отметив верный.

        Решение владельца 31.08.2026: лучше не пустить кривой тест в базу, чем
        потом объяснять, почему у ученика вопрос не засчитался. Система без
        отметки просто не знает, с чем сравнивать ответ.
        """
        if self.block_type != BLOCK_QUESTION:
            return self
        if self.question_type in (None, QUESTION_TEXT):
            return self
        if not any(option.is_correct for option in self.options):
            raise ValueError(
                "У вопроса с вариантами отметьте хотя бы один верный ответ"
            )
        return self

    @field_validator("block_type")
    @classmethod
    def validate_block_type(cls, value: str) -> str:
        value = (value or "").strip()
        if value not in BLOCK_TYPES:
            raise ValueError(f"Неизвестный тип блока: {value}")
        return value

    @field_validator("question_type")
    @classmethod
    def validate_question_type(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if value not in QUESTION_TYPES:
            raise ValueError(f"Неизвестный тип вопроса: {value}")
        return value

    @field_validator("title", "body")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        """Только http и https.

        Ссылка попадает прямо в `href` кнопки на экране ученика. Без этой
        проверки в поле можно вписать не адрес, а исполняемый код (схема
        `javascript:`), и он выполнится у каждого, кому видно задание. Форма
        доступна только главному преподавателю, но уведённый аккаунт бил бы
        сразу по всей школе.
        """
        value = (value or "").strip()
        if not value:
            return None
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("Ссылка должна начинаться с http:// или https://")
        return value


class MockTicketEditPayload(TicketPayload):
    """Билет на правке — та же форма, что у создания (`TicketPayload`), плюс
    `id`: существующий билет правится на месте (текст меняется даже если
    ученик уже сдаёт по нему — снимок билета уже лежит в `MockExamAttempt`,
    владелец подтвердил 30.08.2026), `None` — новый билет, добавленный
    кнопкой «+»."""

    id: int | None = Field(default=None, ge=1)


class MockEditPayload(BaseModel):
    """Правка Пробника (владелец 30.08.2026): билеты, обязательность,
    мини-опрос. Предмет и адресация (теги/поимённые/«всем») не меняются —
    предмет зашит в задание, аудитория переносится с уже существующей темы
    дня. Тариф — исключение (созвон 26.08.2026): его можно включить/снять и
    после создания, как и is_required."""

    model_config = ConfigDict(extra="forbid")

    tickets: list[MockTicketEditPayload] = Field(min_length=1, max_length=10)
    is_required: bool = True
    blocks: list[BlockItem] = Field(default_factory=list, max_length=MAX_BLOCKS)
    tariff_restricted: bool = False
    tariffs: list[str] = Field(default_factory=list, max_length=len(TARIFFS))

    @field_validator("tariffs")
    @classmethod
    def validate_tariffs(cls, value: list[str]) -> list[str]:
        return _validate_tariffs(value)


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


def _validate_tariffs(value: list[str]) -> list[str]:
    """Общий валидатор списка тарифов — переиспользуют все payload'ы с
    тарифным чек-боксом (AudiencePayload, MockEditPayload, SurveyEditPayload)."""
    cleaned = [t.strip().upper() for t in value if t.strip()]
    unknown = [t for t in cleaned if t not in TARIFFS]
    if unknown:
        raise ValueError(f"Неизвестный тариф: {unknown[0]}")
    return list(dict.fromkeys(cleaned))


class AudiencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assign_to_all: bool = False
    tag_ids: list[int] = Field(default_factory=list, max_length=200)
    assignee_usernames: str = Field(default="", max_length=20_000)
    # Тарифная видимость (созвон 26.08.2026) — ортогональна остальной
    # адресации: assign_to_all=True + tariff_restricted=True легальная
    # комбинация («видно всем ученикам этих тарифов»), см. _resolve_audience.
    # По умолчанию выключено (видно всем тарифам, как раньше); включили —
    # список тарифов по умолчанию пуст, то есть элемент скрыт от всех, пока
    # преподаватель явно не отметит нужные (владелец 30.08.2026).
    tariff_restricted: bool = False
    tariffs: list[str] = Field(default_factory=list, max_length=len(TARIFFS))

    @field_validator("tariffs")
    @classmethod
    def validate_tariffs(cls, value: list[str]) -> list[str]:
        return _validate_tariffs(value)


class MockPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subjects: list[SubjectPayload] = Field(min_length=1, max_length=len(MOCK_SUBJECTS))
    audience: AudiencePayload = Field(default_factory=AudiencePayload)
    is_required: bool = True
    # Блоки содержимого — один набор на все выбранные предметы сразу, каждый
    # предмет получает свою копию строк. `BlockItem` (id/None), не голая
    # строка — та же форма, что у правки (`MockEditPayload` ниже), id на
    # создании всегда None, но так payload един на оба эндпоинта и не нужно
    # два разных JS-сборщика.
    blocks: list[BlockItem] = Field(default_factory=list, max_length=MAX_BLOCKS)


def _apply_element_tariff(
    db: DBSession, topic: LearningTopic, *, tariff_restricted: bool, tariffs: list[str]
) -> None:
    """Записать тарифную видимость на тему ОДНОГО элемента — не на тему,
    общую для целой недели.

    `copy_week` (app/services/tracker.py) копирует все элементы недели на одну
    и ту же тему `kind=week`, а не заводит по служебной теме на элемент, как
    `ensure_item_topic` здесь. Записать тариф в этом случае значило бы скрыть
    всю неделю по тарифу вместо одного элемента — тише не ошибиться заранее,
    чем потом объяснять пропавшую неделю (владелец 26.08.2026, разбор
    архитектора 30.08.2026).
    """
    if topic.kind != TOPIC_KIND_PROGRAM_ITEM:
        if tariff_restricted or tariffs:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Тариф элемента нельзя настроить — тема общая для всей "
                    "скопированной недели"
                ),
            )
        return
    set_topic_tariffs(db, topic, tariff_restricted=tariff_restricted, tariffs=tariffs)


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
                tariff_restricted=payload.audience.tariff_restricted,
                tariffs=payload.audience.tariffs,
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
        set_topic_tariffs(
            db,
            topic,
            tariff_restricted=payload.audience.tariff_restricted,
            tariffs=payload.audience.tariffs,
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
        db.flush()
        sync_task_blocks(
            db, task_id=task.id, items=[b.model_dump() for b in payload.blocks]
        )
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
                tariff_restricted=payload.audience.tariff_restricted,
                tariffs=payload.audience.tariffs,
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
    blocks: list[BlockItem] = Field(default_factory=list, max_length=MAX_BLOCKS)
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
    blocks: list[BlockItem] = Field(default_factory=list, max_length=MAX_BLOCKS)
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
    # Мини-опрос после заполнения (владелец 30.08.2026) — не вопросы самой
    # анкеты (`questions` выше), а короткая рефлексия после отправки, та же
    # конструкция, что у остальных семи видов.
    blocks: list[BlockItem] = Field(default_factory=list, max_length=MAX_BLOCKS)
    audience: AudiencePayload = Field(default_factory=AudiencePayload)


class SurveyEditPayload(BaseModel):
    """Правка Анкеты (владелец 30.08.2026) — доступна, только пока анкета не
    используется больше нигде в году и никто ещё не ответил (обе проверки —
    `_edit_payloads`/`update_survey_item`, вторая уже стояла в
    `survey.py::set_questions` до этой стройки). Полная перезапись вопросов,
    не id-сохраняющая правка — безопасно ровно потому, что ответов ещё нет
    (иначе `set_questions` сама откажет)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    questions: list[SurveyQuestionPayload] = Field(default_factory=list, max_length=50)
    is_required: bool = True
    blocks: list[BlockItem] = Field(default_factory=list, max_length=MAX_BLOCKS)
    tariff_restricted: bool = False
    tariffs: list[str] = Field(default_factory=list, max_length=len(TARIFFS))

    @field_validator("tariffs")
    @classmethod
    def validate_tariffs(cls, value: list[str]) -> list[str]:
        return _validate_tariffs(value)


def _audience_reply(db: DBSession, audience: AudiencePayload, tag_ids, not_found) -> dict:
    return {
        "ok": True,
        "not_found": not_found,
        "audience_size": count_topic_audience(
            db,
            assign_to_all=audience.assign_to_all,
            tag_ids=tag_ids,
            assignee_ids=[],
            tariff_restricted=audience.tariff_restricted,
            tariffs=audience.tariffs,
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
    set_topic_tariffs(
        db, topic,
        tariff_restricted=payload.audience.tariff_restricted,
        tariffs=payload.audience.tariffs,
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
    set_topic_tariffs(
        db, topic,
        tariff_restricted=payload.audience.tariff_restricted,
        tariffs=payload.audience.tariffs,
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
    db.flush()
    sync_task_blocks(
        db, task_id=task.id, items=[b.model_dump() for b in payload.blocks]
    )
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
    set_topic_tariffs(
        db, topic,
        tariff_restricted=payload.audience.tariff_restricted,
        tariffs=payload.audience.tariffs,
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
    db.flush()
    sync_task_blocks(
        db, task_id=task.id, items=[b.model_dump() for b in payload.blocks]
    )
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
    set_topic_tariffs(
        db, topic,
        tariff_restricted=payload.audience.tariff_restricted,
        tariffs=payload.audience.tariffs,
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
    db.flush()
    sync_task_blocks(
        db, task_id=task.id, items=[b.model_dump() for b in payload.blocks]
    )
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


def _sync_mock_tickets(
    db: DBSession,
    *,
    assignment: ExamAssignment,
    day: date,
    tickets: list[MockTicketEditPayload],
    assign_to_all: bool,
    tag_ids: list[int],
    assignee_ids: list[int],
    tariff_restricted: bool = False,
    tariffs: list[str] | None = None,
) -> None:
    """Развести билеты из формы правки с уже сохранёнными — id-сохраняющая
    логика, как у мини-опроса (владелец 30.08.2026: править билет можно
    всегда, даже если ученик уже сдаёт по нему — снимок текста уже лежит в
    `MockExamAttempt.ticket_title`/`ticket_description`/`ticket_image_url`,
    правка билета его не трогает).

    Новый билет получает то же окно, что и остальные билеты этого дня
    (`default_schedule_for_day` — окно одно на весь `ExamAssignment`,
    `ticket_number` только для текста ошибок), и ту же адресацию, что уже
    стоит на теме элемента.

    Билет с открытым/сданным циклом (`ExamCycle`) убрать нельзя — проверяем
    явно перед удалением (не полагаемся на FK: в тестах SQLite внешние
    ключи не исполняет, см. video_quiz.py::sync_questions).
    """
    existing = {
        t.id: t
        for t in db.query(ExamTicket).filter(ExamTicket.assignment_id == assignment.id).all()
    }
    next_number = max((t.ticket_number for t in existing.values()), default=0) + 1
    schedule = default_schedule_for_day(day)
    duration_minutes = schedule["duration_minutes"]
    restrict_start_by_duration = True

    matched_ids: set[int] = set()
    for item in tickets:
        ticket = existing.get(item.id) if item.id is not None else None
        if ticket is not None:
            ticket.title = item.title
            ticket.description = item.description
            ticket.image_s3_url = item.image_url
            ticket.image_s3_path = item.image_path
            set_ticket_tariffs(
                db, ticket,
                tariff_restricted=tariff_restricted,
                tariffs=tariffs or [],
            )
            matched_ids.add(ticket.id)
        else:
            opens_at = parse_msk_datetime(
                schedule["opens_at"], ticket_number=next_number, field_label="открывается"
            )
            closes_at = parse_msk_datetime(
                schedule["closes_at"], ticket_number=next_number, field_label="закрывается"
            )
            start_date, end_date = validate_window(
                ticket_number=next_number,
                opens_at=opens_at,
                closes_at=closes_at,
                duration_minutes=duration_minutes,
                restrict_start_by_duration=restrict_start_by_duration,
            )
            create_ticket(
                db,
                assignment,
                number=next_number,
                title=item.title,
                description=item.description,
                image_url=item.image_url,
                image_path=item.image_path,
                opens_at=opens_at,
                closes_at=closes_at,
                duration_minutes=duration_minutes,
                restrict_start_by_duration=restrict_start_by_duration,
                start_date=start_date,
                end_date=end_date,
                assign_to_all=assign_to_all,
                tag_ids=tag_ids,
                assignee_ids=assignee_ids,
                tariff_restricted=tariff_restricted,
                tariffs=tariffs,
            )
            next_number += 1

    removed_ids = [ticket_id for ticket_id in existing if ticket_id not in matched_ids]
    if removed_ids:
        # Явная проверка, а не расчёт на IntegrityError от FK: SQLite в
        # тестах внешние ключи не исполняет вовсе (см. docstring
        # video_quiz.py::sync_questions), проверка должна работать одинаково
        # в тестах и на боевом Postgres.
        busy = (
            db.query(ExamCycle.ticket_id)
            .filter(ExamCycle.ticket_id.in_(removed_ids))
            .distinct()
            .all()
        )
        if busy:
            raise HTTPException(
                status_code=409,
                detail="Нельзя убрать билет — по нему уже есть сдача (открытый или завершённый цикл)",
            )
        for ticket_id in removed_ids:
            db.delete(existing[ticket_id])

    db.flush()


@router.post("/items/{task_id}/mock", response_class=JSONResponse)
def update_mock_item(
    task_id: int,
    payload: MockEditPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    """Правка Пробника (владелец 30.08.2026) — билеты, обязательность,
    мини-опрос. Предмет и адресация не меняются."""
    task = _get_editable_task(db, task_id, ITEM_MOCK_EXAM)
    assignment = db.get(ExamAssignment, task.source_id) if task.source_id else None
    if assignment is None:
        raise HTTPException(status_code=404, detail="Задание не найдено")

    day = msk_date(task.due_at)
    assign_to_all = task.topic_id and db.get(LearningTopic, task.topic_id).assign_to_all or False
    tag_ids = get_tag_ids(db, task.topic_id) if task.topic_id else []
    assignee_ids = get_assignee_ids(db, task.topic_id) if task.topic_id else []

    _sync_mock_tickets(
        db,
        assignment=assignment,
        day=day,
        tickets=payload.tickets,
        assign_to_all=assign_to_all,
        tag_ids=tag_ids,
        assignee_ids=assignee_ids,
        tariff_restricted=payload.tariff_restricted,
        tariffs=payload.tariffs,
    )
    if task.topic_id:
        topic = db.get(LearningTopic, task.topic_id)
        if topic is not None:
            _apply_element_tariff(
                db, topic,
                tariff_restricted=payload.tariff_restricted,
                tariffs=payload.tariffs,
            )
    sync_task_blocks(
        db, task_id=task.id, items=[b.model_dump() for b in payload.blocks]
    )
    update_task(
        task,
        title=task.title,
        description=task.description,
        due_at=task.due_at,
        subject=task.subject,
        assign_to_all=task.assign_to_all,
        kind=ITEM_MOCK_EXAM,
        is_required=payload.is_required,
    )
    db.add(
        AuditLog(
            action="program_mock_update",
            performed_by_id=user["user_id"],
            details=json.dumps({"task_id": task_id}, ensure_ascii=False),
        )
    )
    db.commit()
    return JSONResponse({"ok": True})


def _survey_usage_count(db: DBSession, survey_id: int) -> int:
    """Сколько живых элементов дня ссылаются на эту анкету — переиспользуемый
    шаблон правится, только пока он стоит ровно в одном дне (владелец
    30.08.2026): изменение из карточки одного дня не должно молча менять
    анкету во всех остальных, где она уже показывалась."""
    return (
        db.query(TrackerTask.id)
        .filter(
            TrackerTask.source_kind == SOURCE_SURVEY,
            TrackerTask.source_id == survey_id,
            TrackerTask.deleted_at.is_(None),
        )
        .count()
    )


@router.post("/items/{task_id}/survey", response_class=JSONResponse)
def update_survey_item(
    task_id: int,
    payload: SurveyEditPayload,
    user: Annotated[dict, Depends(require_admin_role)],
    db: Annotated[DBSession, Depends(get_db)],
    _csrf: Annotated[None, Depends(require_csrf_header)],
):
    task = _get_editable_task(db, task_id, ITEM_SURVEY)
    survey = get_survey(db, task.source_id) if task.source_id else None
    if survey is None:
        raise HTTPException(status_code=404, detail="Анкета не найдена")

    if _survey_usage_count(db, survey.id) > 1:
        raise HTTPException(
            status_code=409,
            detail="Эта анкета используется ещё в других днях — правка недоступна, заведите новую",
        )

    update_survey_title(survey, title=payload.title)
    # Кто-то уже ответил — вопросы не трогаем вовсе (не сверяем на совпадение
    # с уже сохранёнными: `set_questions` в любом случае откажет). Остальные
    # поля (title/is_required/мини-опрос) правке не мешают.
    if not survey_has_responses(db, survey.id):
        set_survey_questions(
            db, survey, [q.model_dump() for q in payload.questions]
        )

    if task.topic_id:
        topic = db.get(LearningTopic, task.topic_id)
        if topic is not None:
            topic.title = f"Анкета · {survey.title}"[:200]
            _apply_element_tariff(
                db, topic,
                tariff_restricted=payload.tariff_restricted,
                tariffs=payload.tariffs,
            )

    sync_task_blocks(
        db, task_id=task.id, items=[b.model_dump() for b in payload.blocks]
    )
    update_task(
        task,
        title=survey.title,
        description=task.description,
        due_at=task.due_at,
        subject=task.subject,
        assign_to_all=task.assign_to_all,
        kind=ITEM_SURVEY,
        is_required=payload.is_required,
    )
    db.add(
        AuditLog(
            action="program_survey_update",
            performed_by_id=user["user_id"],
            details=json.dumps({"task_id": task_id, "survey_id": survey.id}, ensure_ascii=False),
        )
    )
    db.commit()
    return JSONResponse({"ok": True})


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
            _apply_element_tariff(
                db, topic,
                tariff_restricted=payload.audience.tariff_restricted,
                tariffs=payload.audience.tariffs,
            )

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
    # Полный текущий список, не дельта (та же семантика, что у остальных
    # полей формы) — пустой список на правке чистит мини-опрос целиком.
    sync_task_blocks(
        db, task_id=task.id, items=[b.model_dump() for b in payload.blocks]
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
            _apply_element_tariff(
                db, topic,
                tariff_restricted=payload.audience.tariff_restricted,
                tariffs=payload.audience.tariffs,
            )

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
    sync_task_blocks(
        db, task_id=task.id, items=[b.model_dump() for b in payload.blocks]
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
            _apply_element_tariff(
                db, topic,
                tariff_restricted=payload.audience.tariff_restricted,
                tariffs=payload.audience.tariffs,
            )

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
