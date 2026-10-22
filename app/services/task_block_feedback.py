"""Обратная связь по TaskBlockSubmission по проверенному паттерну домашки."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.exc import IntegrityError

from app.cache import invalidate_unread
from app.models.notification import Notification
from app.models.task_block import DIALOG_BLOCK_TYPES, TaskBlock, TaskBlockSubmission
from app.models.user import User
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.models.tracker import TrackerTask
from app.services import media_transcode, s3 as s3_service
from app.services.feedback import ROLE_STUDENT, dialog_sender, role_from_rank
from app.services.tz import msk_date_text
from app.services.utils import compress_image

logger = logging.getLogger(__name__)
MAX_FEEDBACK_PHOTO_STORED_SIZE = 10 * 1024 * 1024


def get_or_create_feedback(
    db: DBSession, *, submission_id: int, initiator_id: int,
) -> tuple[TaskBlockFeedback, bool]:
    feedback = db.query(TaskBlockFeedback).filter(
        TaskBlockFeedback.submission_id == submission_id
    ).first()
    if feedback is not None:
        return feedback, False
    feedback = TaskBlockFeedback(submission_id=submission_id, curator_id=initiator_id)
    try:
        # SAVEPOINT сохраняет внешнюю транзакцию пригодной к работе, если два
        # запроса одновременно создают самый первый диалог одной сдачи.
        with db.begin_nested():
            db.add(feedback)
            db.flush()
        return feedback, True
    except IntegrityError:
        existing = db.query(TaskBlockFeedback).filter(
            TaskBlockFeedback.submission_id == submission_id
        ).first()
        if existing is None:
            raise
        return existing, False



def _messages_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "сообщение"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "сообщения"
    return "сообщений"


@dataclass(frozen=True)
class ReplyState:
    """Может ли ученик сейчас ответить в диалоге по своей сдаче.

    `student_text` — подсказка ученику под диалогом, `staff_text` — та же
    причина для сотрудника: куратору видно, ждать ли от ученика вопроса."""

    allowed: bool
    student_text: str
    staff_text: str


def _dialog_open_for(db: DBSession, student: User, tariffs: set[str]) -> bool:
    """Отмечен ли ученик в «Настройке диалога» блока. Годовой — своим тарифом.
    Каникулярный (тарифа нет) — отметкой «Каникулярный набор» **и** включённой
    перепиской у его трека (`IntakeLink.dialog_enabled`, «потенциал»,
    17.10.2026): блок конца недели открыт одному треку, а не всему набору."""
    from app.constants import DIALOG_HOLIDAY, PROGRAM_HOLIDAY
    from app.services.intake_link import tracks_of

    if student.program == PROGRAM_HOLIDAY:
        if DIALOG_HOLIDAY not in tariffs:
            return False
        # Треков бывает несколько (школа 18.10.2026) — хватит одного с перепиской.
        return any(track.dialog_enabled for track in tracks_of(db, student.id))
    return bool(student.tariff) and student.tariff in tariffs


def _is_sent_staff(message: TaskBlockFeedbackMessage) -> bool:
    return message.sender_role != ROLE_STUDENT and not message.is_draft


def reply_limit(db: DBSession, submission: TaskBlockSubmission) -> int | None:
    """Сколько сообщений ученик может написать в диалоге по этой сдаче;
    None — ответ ему закрыт: тип блока без сдачи или тариф **сейчас** (О6)
    не отмечен в «Настройке диалога» (пусто = закрыто всем, О9)."""
    # Импорт здесь: `task_blocks` тянет за собой половину доменных сервисов.
    from app.services.task_blocks import get_dialog_tariffs

    block = db.get(TaskBlock, submission.block_id)
    if block is None or block.block_type not in DIALOG_BLOCK_TYPES:
        return None
    student = db.get(User, submission.user_id)
    tariffs = get_dialog_tariffs(db, [block.id]).get(block.id, set())
    if student is None or not _dialog_open_for(db, student, tariffs):
        return None
    return block.dialog_reply_limit or 1


def student_can_reply(
    db: DBSession, submission: TaskBlockSubmission,
    feedback: TaskBlockFeedback | None,
) -> ReplyState:
    """**Единственное место правила «может ли ученик ответить на ОС»**
    (владелец 01.10.2026, план `plans/2026-10-01-apparchi-call-30-09-followup.md`,
    О1–О9, О22). Читают роут `student_message` и экран диалога.

    Порядок проверок:
    0. Сотрудник нажал «Завершить ОС» — диалог закрыт для обеих сторон
       (ОС, фаза 2, О25), дальше ученик только оценивает.
    1. Блок вне `DIALOG_BLOCK_TYPES` — ответа нет никогда. Сейчас туда входят
       все три вида сдачи, включая контрольную на время (владелец 01.10.2026:
       оставить и её настраиваемой); проверка стоит на случай нового типа
       сдачи, у которого настройки ещё нет.
    2. Тариф ученика **сейчас** (О6, а не на момент сдачи) не отмечен в
       «Настройке диалога» блока — ответа нет. Пусто = закрыто всем (О9).
    3. Преподаватель ещё не отправил ОС — ждать. Черновик не считается:
       ученик его не видит (владелец 22.10.2026).
    4. Ученик уже отправил `dialog_reply_limit` сообщений — лимит исчерпан.
       Считаются только его сообщения, одна отправка — одно (О3); куратор
       пишет без ограничений (О4), срока у диалога нет (О5).
    """
    closed = ReplyState(
        False,
        "На эту обратную связь ответить нельзя.",
        "Ученик не может ответить на эту обратную связь.",
    )
    if feedback is not None and feedback.feedback_closed_at is not None:
        return ReplyState(
            False,
            "Преподаватель завершил обратную связь – диалог закрыт.",
            "Обратная связь завершена – диалог закрыт.",
        )
    limit = reply_limit(db, submission)
    if limit is None:
        return closed
    messages = feedback.messages if feedback is not None else []
    if not any(_is_sent_staff(message) for message in messages):
        return ReplyState(
            False,
            "Задать вопрос можно после первого сообщения преподавателя.",
            f"Ученик сможет задать вопрос: до {limit} {_messages_word(limit)}.",
        )
    sent = sum(1 for message in messages if message.sender_role == ROLE_STUDENT)
    left = limit - sent
    if left <= 0:
        return ReplyState(
            False,
            "Вопрос отправлен – ответ преподавателя придёт сюда.",
            "Ученик отправил все свои сообщения. Ответить ему можно.",
        )
    return ReplyState(
        True,
        f"Можно отправить ещё {left} {_messages_word(left)}.",
        f"Ученик может отправить ещё {left} {_messages_word(left)}.",
    )


def draft_messages(
    feedback: TaskBlockFeedback | None,
) -> list[TaskBlockFeedbackMessage]:
    if feedback is None:
        return []
    return [message for message in feedback.messages if message.is_draft]


def close_refusal(
    db: DBSession, submission: TaskBlockSubmission,
    feedback: TaskBlockFeedback | None,
) -> str | None:
    """**Единственное место правила «можно ли нажать «Завершить ОС»»**
    (владелец 22.10.2026). Читают роут `close_feedback` и экран диалога.

    Кнопка появляется, когда ученику больше нечего спросить: ОС отправлена,
    черновиков нет, и либо ответ ему закрыт «Настройкой диалога», либо он
    потратил свой лимит и на последний вопрос уже ответили. До 22.10.2026
    завершить можно было сразу после первого сообщения — и ученик, узнав об
    ОС, уже не мог задать вопрос. None — завершить можно."""
    messages = feedback.messages if feedback is not None else []
    if not any(_is_sent_staff(message) for message in messages):
        return "Завершить можно после того, как ОС отправлена ученику."
    if any(message.is_draft for message in messages):
        return "Сначала отправь ученику новые сообщения – кнопка «Отправить ОС»."
    limit = reply_limit(db, submission)
    if limit is None:
        return None
    sent = sum(1 for message in messages if message.sender_role == ROLE_STUDENT)
    if sent < limit:
        left = limit - sent
        return (
            f"Ученик ещё может задать вопрос: осталось {left} {_messages_word(left)}. "
            "«Завершить ОС» появится, когда он их потратит."
        )
    if messages[-1].sender_role == ROLE_STUDENT:
        return "Ответь на последний вопрос ученика – потом можно завершить."
    return None


def send_drafts(feedback: TaskBlockFeedback) -> int:
    """«Отправить ОС»: открыть ученику все черновики сотрудников разом.
    Возвращает их число; уведомление шлёт роут — одно на отправку."""
    drafts = draft_messages(feedback)
    for message in drafts:
        message.is_draft = False
    return len(drafts)

async def _upload_photo(
    submission_id: int, filename: str, data: bytes,
) -> tuple[str, str] | None:
    loop = asyncio.get_running_loop()
    path = s3_service.s3_path_task_block_feedback(submission_id, filename)

    def _do() -> tuple[bytes, str | None]:
        compressed = compress_image(data)
        if len(compressed) > MAX_FEEDBACK_PHOTO_STORED_SIZE:
            raise ValueError("Фото после сжатия превышает 10 МБ")
        return compressed, s3_service.upload_to_s3(path, compressed, "image/jpeg")

    try:
        _, url = await loop.run_in_executor(None, _do)
    except ValueError:
        raise
    except Exception as exc:
        logger.warning(
            "task block feedback photo upload failed for submission_id=%s: %s",
            submission_id, exc,
        )
        raise ValueError("Не удалось загрузить фото. Попробуй ещё раз.") from exc
    if not url:
        raise ValueError("Не удалось загрузить фото. Попробуй ещё раз.")
    return path, (url or "")


async def _upload_video(
    submission_id: int, filename: str, data: bytes, content_type: str, *, note: bool = False,
) -> tuple[str, str] | None:
    """Положить видео в S3. Обычное — как есть, кружок (`note`) — перегнать в
    mp4, который играет любой телефон (`media_transcode`). Returns (s3_path, s3_url) или None."""
    loop = asyncio.get_running_loop()
    ct = content_type or "video/mp4"

    def _do() -> tuple[str, str | None]:
        name, payload, mime = (
            media_transcode.playable_note(filename, data, ct) if note else (filename, data, ct)
        )
        path = s3_service.s3_path_task_block_feedback(submission_id, name)
        return path, s3_service.upload_to_s3(path, payload, mime)

    try:
        path, url = await loop.run_in_executor(None, _do)
    except Exception as exc:
        logger.warning(
            "task block feedback video upload failed for submission_id=%s: %s",
            submission_id, exc,
        )
        raise ValueError("Не удалось загрузить видео. Попробуй ещё раз.") from exc
    if not url:
        raise ValueError("Не удалось загрузить видео. Попробуй ещё раз.")
    return path, url


async def _upload_audio(
    submission_id: int, filename: str, data: bytes, content_type: str,
) -> tuple[str, str] | None:
    """Перегнать голосовое в m4a, который играет любой телефон (`media_transcode`),
    и положить в S3. Returns (s3_path, s3_url) или None."""
    loop = asyncio.get_running_loop()
    ct = content_type or "audio/mpeg"

    def _do() -> tuple[str, str | None]:
        name, payload, mime = media_transcode.playable_voice(filename, data, ct)
        path = s3_service.s3_path_task_block_feedback(submission_id, name)
        return path, s3_service.upload_to_s3(path, payload, mime)

    try:
        path, url = await loop.run_in_executor(None, _do)
    except Exception as exc:
        logger.warning(
            "task block feedback audio upload failed for submission_id=%s: %s",
            submission_id, exc,
        )
        raise ValueError("Не удалось загрузить голосовое. Попробуй ещё раз.") from exc
    if not url:
        raise ValueError("Не удалось загрузить голосовое. Попробуй ещё раз.")
    return path, url


async def send_message(
    db: DBSession, *, feedback: TaskBlockFeedback, sender_id: int, sender_role: str,
    text: str | None, photo: tuple[str, bytes] | None,
    video: tuple[str, bytes, str] | None = None,
    audio: tuple[str, bytes, str] | None = None,
    video_link: str | None = None,
    video_is_note: bool = False,
) -> TaskBlockFeedbackMessage:
    """По образцу `app/services/feedback.py::send_message` (владелец
    17.09.2026: у диалога по блокам задания должны быть те же вложения,
    что у эталонного диалога пробника/портфолио)."""
    text_clean = (text or "").strip() or None
    photo_path = None
    photo_url = None
    if photo is not None:
        uploaded = await _upload_photo(feedback.submission_id, photo[0], photo[1])
        if uploaded is not None:
            photo_path, photo_url = uploaded
    video_path = None
    video_url = None
    if video is not None:
        uploaded = await _upload_video(
            feedback.submission_id, video[0], video[1], video[2],
            note=video_is_note and sender_role != ROLE_STUDENT,
        )
        if uploaded is not None:
            video_path, video_url = uploaded
    audio_path = None
    audio_url = None
    if audio is not None:
        uploaded = await _upload_audio(feedback.submission_id, audio[0], audio[1], audio[2])
        if uploaded is not None:
            audio_path, audio_url = uploaded
    if (
        text_clean is None and photo_url is None and video_url is None
        and audio_url is None and not video_link
    ):
        raise ValueError("Сообщение должно содержать текст, фото, видео, ссылку на видео или голосовое")
    message = TaskBlockFeedbackMessage(
        feedback_id=feedback.id,
        sender_id=sender_id,
        sender_role=sender_role,
        text=text_clean,
        photo_s3_path=photo_path,
        photo_s3_url=photo_url,
        video_s3_path=video_path,
        video_s3_url=video_url,
        audio_s3_path=audio_path,
        audio_s3_url=audio_url,
        video_url=video_link,
        # Кружок записывает только преподаватель (владелец 25.09.2026), и
        # флаг имеет смысл лишь при загруженном видео — проверка здесь, а не в
        # трёх роутах, чтобы ученик не мог прислать круг в обход формы.
        video_is_note=bool(
            video_is_note and video_url is not None and sender_role != ROLE_STUDENT
        ),
        # Сотрудник пишет черновиком, ученику его открывает «Отправить ОС»
        # (владелец 22.10.2026). Ученик пишет сразу — его ждут.
        is_draft=sender_role != ROLE_STUDENT,
    )
    db.add(message)
    db.flush()
    return message


def group_works_label(
    submission: TaskBlockSubmission, linked: list[TaskBlockSubmission] | None,
) -> str | None:
    """«Общая обратная связь: работы с 04.10.2026 по 13.10.2026 (3)» — для
    карточки ученика и уведомления. None — работа одна."""
    if not linked:
        return None
    dates = [submission.submitted_at] + [w.submitted_at for w in linked]
    first, last = msk_date_text(min(dates)), msk_date_text(max(dates))
    span = f"с {first} по {last}" if first != last else f"за {first}"
    return f"Общая обратная связь: работы {span} ({len(linked) + 1})"


def notify_counterpart(
    db: DBSession, *, submission: TaskBlockSubmission, recipient_id: int,
    sender_role: str,
) -> Notification:
    title = (
        "Ученик ответил по сданной работе"
        if sender_role == ROLE_STUDENT
        else "Преподаватель оставил обратную связь по работе"
    )
    text = f"По сданной работе #{submission.id} есть новое сообщение."
    if sender_role != ROLE_STUDENT:
        # Общая ОС (13.10.2026): ученик должен понять, что ответ на все работы.
        from app.services.linked_feedback import absorbed_for_mains

        label = group_works_label(submission, absorbed_for_mains(db, [submission]).get(submission.id))
        if label:
            title = "Преподаватель оставил обратную связь по работам"
            text = f"{label}. Есть новое сообщение."
    notification = Notification(
        user_id=recipient_id,
        title=title,
        text=text,
        task_block_submission_id=submission.id,
    )
    db.add(notification)
    db.flush()
    invalidate_unread(recipient_id)
    return notification


def resubmission_notification(
    db: DBSession, *, submission: TaskBlockSubmission,
) -> Notification | None:
    """Ученик заменил работу, возвращённую на доработку, — сказать проверяющему
    (владелец 14.10.2026). Иначе куратор, написавший «доделай», узнал бы о новой
    версии только по списку «На проверку». Получатель — автор диалога ОС, а без
    диалога — куратор ученика. Зовёт `upload_task_block_work` только для
    сдачи, которая была на доработке, после того как работа снова сдана."""
    student = db.get(User, submission.user_id)
    recipient_id = db.query(TaskBlockFeedback.curator_id).filter(
        TaskBlockFeedback.submission_id == submission.id
    ).scalar() or (student.curator_id if student is not None else None)
    if recipient_id is None or recipient_id == submission.user_id:
        return None
    block = db.get(TaskBlock, submission.block_id)
    task = db.get(TrackerTask, block.task_id) if block is not None else None
    who = (
        f"{student.last_name or ''} {student.first_name or student.name}".strip()
        if student is not None else "Ученик"
    )
    where = f" по заданию «{task.title}»" if task is not None else ""
    notification = Notification(
        user_id=recipient_id,
        title="Ученик загрузил исправленную работу",
        text=f"{who}{where} – работа заменена после доработки, нужна проверка.",
        task_block_submission_id=submission.id,
    )
    db.add(notification)
    db.flush()
    invalidate_unread(recipient_id)
    return notification


def serialize_messages(
    messages: list[TaskBlockFeedbackMessage], names: dict[int, str] | None = None,
) -> list[dict]:
    names = names or {}
    result = []
    for message in messages:
        sender_name, sender_role_label = dialog_sender(
            message.sender_role, names.get(message.sender_id),
        )
        result.append({
            "id": message.id,
            "sender_id": message.sender_id,
            "sender_role": message.sender_role,
            "sender_name": sender_name,
            "sender_role_label": sender_role_label,
            "text": message.text,
            "photo_s3_url": message.photo_s3_url,
            "video_s3_url": message.video_s3_url,
            "video_url": message.video_url,
            "audio_s3_url": message.audio_s3_url,
            "video_is_note": bool(message.video_is_note),
            "created_at": message.created_at.isoformat() if message.created_at else None,
            "edited": message.edited_at is not None,
            "draft": bool(message.is_draft),
        })
    return result


def list_student_feedback_cards(
    db: DBSession, user_id: int,
) -> tuple[list[dict], list[dict]]:
    """ОС преподавателей по работам ученика для вкладки «Обратная связь»
    (`/cabinet/cycle`): открытые и завершённые («Завершить ОС»), свежие сверху.

    Владелец 04.10.2026: «во вкладке обратная связь должна быть обратная
    связь, которую оставляют преподаватели». До этого вкладка показывала
    только пробники, а диалог по сдаче в задании ученик находил лишь через
    карточку задания или колокольчик. Попадает диалог, где сотрудник уже
    написал: пустой диалог ученик открыть может, но ОС в нём ещё нет."""
    staff_last = (
        db.query(
            TaskBlockFeedbackMessage.feedback_id,
            func.max(TaskBlockFeedbackMessage.created_at).label("last_at"),
        )
        .filter(
            TaskBlockFeedbackMessage.sender_role != ROLE_STUDENT,
            # Черновик ученику не виден (22.10.2026): диалог без отправленной
            # ОС в список не попадает.
            TaskBlockFeedbackMessage.is_draft.is_(False),
        )
        .group_by(TaskBlockFeedbackMessage.feedback_id)
        .subquery()
    )
    rows = (
        db.query(TaskBlockFeedback, TaskBlockSubmission, TaskBlock, TrackerTask, staff_last.c.last_at)
        .join(staff_last, staff_last.c.feedback_id == TaskBlockFeedback.id)
        .join(TaskBlockSubmission, TaskBlockSubmission.id == TaskBlockFeedback.submission_id)
        .join(TaskBlock, TaskBlock.id == TaskBlockSubmission.block_id)
        .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
        .filter(TaskBlockSubmission.user_id == user_id)
        .order_by(staff_last.c.last_at.desc(), TaskBlockFeedback.id.desc())
        .all()
    )
    open_cards: list[dict] = []
    closed_cards: list[dict] = []
    if not rows:
        return open_cards, closed_cards

    submission_ids = [submission.id for _f, submission, _b, _t, _at in rows]
    unread = dict(
        db.query(Notification.task_block_submission_id, func.count(Notification.id))
        .filter(
            Notification.user_id == user_id,
            Notification.task_block_submission_id.in_(submission_ids),
            Notification.is_read.is_(False),
        )
        .group_by(Notification.task_block_submission_id)
        .all()
    )
    # Общая ОС на несколько заданий (13.10.2026): одна карточка на все
    # работы, в подписи — их период и число.
    from app.services.linked_feedback import absorbed_for_mains

    groups = absorbed_for_mains(db, [submission for _f, submission, _b, _t, _at in rows])
    for feedback, submission, block, task, last_at in rows:
        card = {
            "href": f"/cabinet/task-block-submissions/{submission.id}/feedback",
            "task_title": task.title,
            "block_title": block.title,
            "group_label": group_works_label(submission, groups.get(submission.id)),
            "last_at": msk_date_text(last_at),
            "closed_at": msk_date_text(feedback.feedback_closed_at) or None,
            "unread_count": unread.get(submission.id, 0),
            "score": float(submission.score) if submission.score is not None else None,
        }
        (closed_cards if card["closed_at"] else open_cards).append(card)
    return open_cards, closed_cards


UNSENT_FIRST_AFTER = timedelta(hours=1)
UNSENT_REPEAT_EVERY = timedelta(days=1)


def run_unsent_feedback_reminders(db: DBSession, now: datetime | None = None) -> list[int]:
    """Напомнить сотруднику, что ОС написана, а ученику не отправлена
    (владелец 22.10.2026). Первое — через час после последнего черновика
    (куратор, скорее всего, закончил), дальше раз в сутки, пока черновик
    лежит. Получает каждый, чей черновик в диалоге. Повторы держит
    `StudentReminder` (вид `feedback_unsent`). Коммитит сам; возвращает id
    уведомлений — рассылку делает планировщик после коммита."""
    from app.models.student_reminder import KIND_FEEDBACK_UNSENT, StudentReminder
    from app.services.feedback_rating import _absolute

    now = now or datetime.now(timezone.utc)
    rows = (
        db.query(
            TaskBlockFeedbackMessage.feedback_id,
            func.min(TaskBlockFeedbackMessage.id),
            func.max(TaskBlockFeedbackMessage.created_at),
        )
        .join(TaskBlockFeedback, TaskBlockFeedback.id == TaskBlockFeedbackMessage.feedback_id)
        .filter(
            TaskBlockFeedbackMessage.is_draft.is_(True),
            TaskBlockFeedback.feedback_closed_at.is_(None),
        )
        .group_by(TaskBlockFeedbackMessage.feedback_id)
        .all()
    )
    created: list[Notification] = []
    for feedback_id, first_draft_id, last_at in rows:
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        quiet = now - last_at
        if quiet < UNSENT_FIRST_AFTER:
            continue
        day = int((quiet - UNSENT_FIRST_AFTER) / UNSENT_REPEAT_EVERY)
        ref = f"{feedback_id}:{first_draft_id}:{day}"
        feedback = db.get(TaskBlockFeedback, feedback_id)
        submission = db.get(TaskBlockSubmission, feedback.submission_id)
        student = db.get(User, submission.user_id)
        block = db.get(TaskBlock, submission.block_id)
        task = db.get(TrackerTask, block.task_id) if block is not None else None
        who = (
            f"{student.last_name or ''} {student.first_name or student.name}".strip()
            if student is not None else "Ученик"
        )
        where = f", «{task.title}»" if task is not None else ""
        link = _absolute(f"/cabinet/staff/task-block-submissions/{submission.id}/feedback")
        authors = {message.sender_id for message in draft_messages(feedback)}
        for author_id in sorted(authors):
            author = db.get(User, author_id)
            if author is None or not author.is_active or author.deleted_at is not None:
                continue
            if db.query(StudentReminder.id).filter(
                StudentReminder.user_id == author_id,
                StudentReminder.kind == KIND_FEEDBACK_UNSENT,
                StudentReminder.ref == ref,
            ).first():
                continue
            notification = Notification(
                user_id=author_id,
                title="Обратная связь не отправлена",
                text=(
                    f"{who}{where}: ученик не видит твои сообщения, пока ты не нажмёшь "
                    f"«Отправить ОС». Открыть диалог: {link}"
                ),
                task_block_submission_id=submission.id,
            )
            db.add(notification)
            db.add(StudentReminder(user_id=author_id, kind=KIND_FEEDBACK_UNSENT, ref=ref))
            created.append(notification)
    db.commit()
    for notification in created:
        invalidate_unread(notification.user_id)
    if created:
        logger.info("Неотправленная ОС: %d напоминаний сотрудникам", len(created))
    return [notification.id for notification in created]


__all__ = [
    "ReplyState", "get_or_create_feedback", "list_student_feedback_cards",
    "notify_counterpart", "resubmission_notification", "role_from_rank", "send_message",
    "serialize_messages", "student_can_reply", "close_refusal", "draft_messages",
    "send_drafts", "reply_limit", "run_unsent_feedback_reminders",
]
