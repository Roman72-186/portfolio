"""Обратная связь по TaskBlockSubmission по проверенному паттерну домашки."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session as DBSession
from sqlalchemy.exc import IntegrityError

from app.cache import invalidate_unread
from app.models.notification import Notification
from app.models.task_block import DIALOG_BLOCK_TYPES, TaskBlock, TaskBlockSubmission
from app.models.user import User
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.services import media_transcode, s3 as s3_service
from app.services.feedback import ROLE_STUDENT, role_from_rank, role_label_ru
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


def student_can_reply(
    db: DBSession, submission: TaskBlockSubmission,
    feedback: TaskBlockFeedback | None,
) -> ReplyState:
    """**Единственное место правила «может ли ученик ответить на ОС»**
    (владелец 01.10.2026, план `plans/2026-10-01-apparchi-call-30-09-followup.md`,
    О1–О9, О22). Читают роут `student_message` и экран диалога.

    Порядок проверок:
    1. Блок вне `DIALOG_BLOCK_TYPES` — ответа нет никогда. Это контрольная на
       время: «просто получить обратку и всё, на любом тарифе» (созвон
       00:08:51–00:09:09).
    2. Тариф ученика **сейчас** (О6, а не на момент сдачи) не отмечен в
       «Настройке диалога» блока — ответа нет. Пусто = закрыто всем (О9).
    3. Преподаватель ещё не написал — ждать первого сообщения.
    4. Ученик уже отправил `dialog_reply_limit` сообщений — лимит исчерпан.
       Считаются только его сообщения, одна отправка — одно (О3); куратор
       пишет без ограничений (О4), срока у диалога нет (О5).
    """
    # Импорт здесь: `task_blocks` тянет за собой половину доменных сервисов.
    from app.services.task_blocks import get_dialog_tariffs

    closed = ReplyState(
        False,
        "На эту обратную связь ответить нельзя.",
        "Ученик не может ответить на эту обратную связь.",
    )
    block = db.get(TaskBlock, submission.block_id)
    if block is None or block.block_type not in DIALOG_BLOCK_TYPES:
        return closed
    student = db.get(User, submission.user_id)
    tariffs = get_dialog_tariffs(db, [block.id]).get(block.id, set())
    if student is None or student.tariff not in tariffs:
        return closed
    limit = block.dialog_reply_limit or 1
    messages = feedback.messages if feedback is not None else []
    if not any(message.sender_role != ROLE_STUDENT for message in messages):
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
    )
    db.add(message)
    db.flush()
    return message


def notify_counterpart(
    db: DBSession, *, submission: TaskBlockSubmission, recipient_id: int,
    sender_role: str,
) -> Notification:
    title = (
        "Ученик ответил по сданной работе"
        if sender_role == ROLE_STUDENT
        else "Преподаватель оставил обратную связь по работе"
    )
    notification = Notification(
        user_id=recipient_id,
        title=title,
        text=f"По сданной работе #{submission.id} есть новое сообщение.",
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
    return [
        {
            "id": message.id,
            "sender_id": message.sender_id,
            "sender_role": message.sender_role,
            "sender_name": names.get(message.sender_id),
            "sender_role_label": role_label_ru(message.sender_role),
            "text": message.text,
            "photo_s3_url": message.photo_s3_url,
            "video_s3_url": message.video_s3_url,
            "video_url": message.video_url,
            "audio_s3_url": message.audio_s3_url,
            "video_is_note": bool(message.video_is_note),
            "created_at": message.created_at.isoformat() if message.created_at else None,
        }
        for message in messages
    ]


__all__ = [
    "ReplyState", "get_or_create_feedback", "notify_counterpart", "role_from_rank",
    "send_message", "serialize_messages", "student_can_reply",
]
