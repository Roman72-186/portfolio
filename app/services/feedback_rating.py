"""ОС, фаза 2: «Завершить ОС», оценка 1–5, служебный топик.

Созвон 30.09.2026, правила подтверждены владельцем 01.10.2026 — план
`plans/2026-10-01-apparchi-call-30-09-followup.md`, О10–О26, инвариант
`docs/invariants/feedback.md`. Одни правила на оба диалога ОС — сдачу в блоке
задания (`TaskBlockFeedback`) и пробник (`Feedback`): у обоих одинаковые поля
`feedback_closed_at`/`feedback_closed_by_id` и список `messages`, поэтому
функции ниже принимают любой из них.

Порядок жизни диалога:
1. Сотрудник пишет ОС (одно или несколько сообщений).
2. Нажимает «Завершить ОС» (`close_dialog`) — диалог закрыт для обоих,
   ученику уходит одно уведомление «оцени ОС» (`rate_request_notification`).
3. Ученик по желанию ставит 1–5 с обязательным комментарием и до трёх
   скриншотов (`create_rating`); изменить нельзя.
4. Оценка уходит в служебный топик, скриншоты — картинками
   (`send_rating_to_care_topic`, фоновой задачей после коммита).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.config import settings
from app.db.database import SessionLocal
from app.models.feedback_rating import (
    DIALOG_MOCK_EXAM,
    DIALOG_TASK_BLOCK,
    FEEDBACK_CONTROL,
    FEEDBACK_HOMEWORK,
    FEEDBACK_TYPE_LABELS,
    RATING_COMMENT_MAX,
    RATING_MAX,
    RATING_MAX_SCREENSHOTS,
    RATING_MIN,
    FeedbackRating,
    FeedbackRatingImage,
)
from app.models.notification import Notification
from app.models.task_block import BLOCK_TIMED
from app.models.user import User
from app.services import s3 as s3_service, telegram as telegram_service
from app.services.feedback import ROLE_STUDENT
from app.services.upload_validation import read_image_uploads
from app.services.utils import compress_image

logger = logging.getLogger(__name__)

MAX_SCREENSHOT_STORED_SIZE = 10 * 1024 * 1024
MAX_SCREENSHOT_INPUT_SIZE = 25 * 1024 * 1024


class RatingError(Exception):
    """Отказ с HTTP-статусом и текстом для человека: роут отдаёт его как есть."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def has_staff_message(dialog) -> bool:
    return dialog is not None and any(
        message.sender_role != ROLE_STUDENT for message in dialog.messages
    )


def is_closed(dialog) -> bool:
    return dialog is not None and dialog.feedback_closed_at is not None


def close_dialog(dialog, user_id: int) -> bool:
    """«Завершить ОС» (О25). True — закрыли сейчас, False — уже было закрыто:
    повторное нажатие ничего не делает и второго уведомления не шлёт (О26).

    Закрыть можно только диалог, где сотрудник уже написал: иначе ученику
    пришло бы «оцени ОС», которой нет."""
    if dialog is not None and dialog.feedback_closed_at is not None:
        return False
    if not has_staff_message(dialog):
        raise RatingError(409, "Сначала напишите ученику обратную связь.")
    dialog.feedback_closed_at = datetime.now(timezone.utc)
    dialog.feedback_closed_by_id = user_id
    return True


def feedback_type_for_block(block_type: str) -> str:
    """Вид ОС берётся из типа блока сдачи (О16)."""
    return FEEDBACK_CONTROL if block_type == BLOCK_TIMED else FEEDBACK_HOMEWORK


def get_rating(db: DBSession, dialog_kind: str, dialog_id: int | None) -> FeedbackRating | None:
    if dialog_id is None:
        return None
    return db.query(FeedbackRating).filter(
        FeedbackRating.dialog_kind == dialog_kind,
        FeedbackRating.dialog_id == dialog_id,
    ).first()


def serialize_rating(rating: FeedbackRating | None) -> dict | None:
    if rating is None:
        return None
    return {
        "score": rating.score,
        "comment": rating.comment,
        "images": [image.image_s3_url for image in rating.images],
        "created_at": rating.created_at,
    }


def rating_panel(
    db: DBSession, *, dialog_kind: str, dialog, viewer_role: str,
    close_url: str, rating_url: str,
) -> dict:
    """Данные общего блока «Завершить ОС / оценка»
    (`partials/feedback_rating.html`) — одни на оба экрана диалога."""
    closed = is_closed(dialog)
    is_staff = viewer_role != "student"
    return {
        "is_staff": is_staff,
        "closed": closed,
        "can_close": is_staff and not closed and has_staff_message(dialog),
        "close_url": close_url,
        "rating": serialize_rating(get_rating(db, dialog_kind, dialog.id if dialog else None)),
        "rating_url": rating_url,
        "max_screenshots": RATING_MAX_SCREENSHOTS,
        "comment_max": RATING_COMMENT_MAX,
    }


def parse_rating_input(score: str, comment: str) -> tuple[int, str]:
    """Оценка 1–5 и непустой комментарий при любой оценке (О12, О23)."""
    try:
        value = int((score or "").strip())
    except ValueError:
        raise RatingError(422, "Выбери оценку от 1 до 5.")
    if not RATING_MIN <= value <= RATING_MAX:
        raise RatingError(422, "Выбери оценку от 1 до 5.")
    text = (comment or "").strip()
    if not text:
        raise RatingError(422, "Напиши комментарий – без него оценку не отправить.")
    if len(text) > RATING_COMMENT_MAX:
        raise RatingError(422, f"Комментарий длиннее {RATING_COMMENT_MAX} символов.")
    return value, text


async def read_rating_form(
    score: str, comment: str, screenshots: list | None,
) -> tuple[int, str, list[tuple[str, bytes]]]:
    """Оценка, комментарий и байты скриншотов из формы — одно чтение на оба
    роута оценки. Пустое поле выбора файлов приходит частью без имени."""
    value, text = parse_rating_input(score, comment)
    files = [item for item in (screenshots or []) if getattr(item, "filename", "")]
    if not files:
        return value, text, []
    images, error = await read_image_uploads(
        files, max_files=RATING_MAX_SCREENSHOTS, max_size=MAX_SCREENSHOT_INPUT_SIZE,
        too_many_error="Можно приложить до {max_files} скриншотов.",
        too_large_error="Скриншот «{filename}» больше 25 МБ.",
    )
    if error:
        raise RatingError(422, error)
    return value, text, images


async def _upload_screenshot(student_id: int, filename: str, data: bytes) -> tuple[str, str]:
    loop = asyncio.get_running_loop()
    path = s3_service.s3_path_feedback_rating(student_id, filename)

    def _do() -> str | None:
        compressed = compress_image(data)
        if len(compressed) > MAX_SCREENSHOT_STORED_SIZE:
            raise ValueError("Скриншот после сжатия больше 10 МБ.")
        return s3_service.upload_to_s3(path, compressed, "image/jpeg")

    try:
        url = await loop.run_in_executor(None, _do)
    except ValueError as exc:
        raise RatingError(422, str(exc)) from exc
    except Exception as exc:
        logger.warning("feedback rating screenshot upload failed student_id=%s: %s", student_id, exc)
        raise RatingError(502, "Не удалось загрузить скриншот. Попробуй ещё раз.") from exc
    if not url:
        raise RatingError(502, "Не удалось загрузить скриншот. Попробуй ещё раз.")
    return path, url


def _forget_uploads(paths: list[str]) -> None:
    for path in paths:
        try:
            s3_service.delete_from_s3(path)
        except Exception:
            logger.warning("feedback rating: не удалось удалить скриншот %s", path)


async def create_rating(
    db: DBSession, *, dialog_kind: str, dialog, student_id: int,
    feedback_type: str, task_id: int | None, task_title: str | None,
    score: int, comment: str, screenshots: list[tuple[str, bytes]],
) -> FeedbackRating:
    """Сохранить оценку ученика. Форма появляется только после «Завершить ОС»
    (О10/О14б), оценка одна на диалог и окончательная (О14, О24)."""
    if not is_closed(dialog):
        raise RatingError(403, "Оценить можно, когда преподаватель завершит обратную связь.")
    already = "Оценка уже отправлена – изменить её нельзя."
    if get_rating(db, dialog_kind, dialog.id) is not None:
        raise RatingError(409, already)
    if len(screenshots) > RATING_MAX_SCREENSHOTS:
        raise RatingError(422, f"Можно приложить до {RATING_MAX_SCREENSHOTS} скриншотов.")

    uploaded: list[tuple[str, str]] = []
    try:
        for filename, data in screenshots:
            uploaded.append(await _upload_screenshot(student_id, filename, data))
    except RatingError:
        _forget_uploads([path for path, _ in uploaded])
        raise

    rating = FeedbackRating(
        dialog_kind=dialog_kind,
        dialog_id=dialog.id,
        student_id=student_id,
        # Оценивают автора ОС — того, кто начал диалог, а не того, кто нажал
        # «Завершить ОС»: закрывать могут ГП и суперадмин за куратора
        # (владелец 05.10.2026). То же поле считает «дал ОС» в статистике.
        curator_id=dialog.curator_id,
        feedback_type=feedback_type,
        task_id=task_id,
        task_title=(task_title or None) and task_title[:300],
        score=score,
        comment=comment,
        images=[
            FeedbackRatingImage(image_s3_path=path, image_s3_url=url, sort_order=index)
            for index, (path, url) in enumerate(uploaded)
        ],
    )
    try:
        # SAVEPOINT: два одновременных запроса ученика упрутся в уникальную
        # пару «вид диалога + id», второй получит 409, а не 500.
        with db.begin_nested():
            db.add(rating)
            db.flush()
    except IntegrityError:
        _forget_uploads([path for path, _ in uploaded])
        raise RatingError(409, already)
    return rating


def _absolute(path: str) -> str:
    return f"https://{settings.domain}{path}" if settings.domain else path


def rate_request_notification(
    db: DBSession, *, student_id: int, subject: str, link_path: str,
    task_block_submission_id: int | None = None, work_id: int | None = None,
) -> Notification:
    """Одно уведомление «оцени ОС» (О26): колокольчик + личное сообщение бота.
    Ссылка на диалог — в тексте: бот шлёт заголовок и текст, ссылки
    уведомления (`task_block_submission_id`, `work_id`) он не видит."""
    notification = Notification(
        user_id=student_id,
        title="Оцени обратную связь",
        text=(
            f"Преподаватель завершил обратную связь – {subject}. "
            f"Поставь оценку от 1 до 5 и напиши пару слов: {_absolute(link_path)}"
        ),
        task_block_submission_id=task_block_submission_id,
        work_id=work_id,
    )
    db.add(notification)
    db.flush()
    invalidate_unread(student_id)
    return notification


def _person(user: User | None) -> str:
    if user is None:
        return "—"
    full = f"{user.last_name or ''} {user.first_name or user.name or ''}".strip()
    return full or (user.name or f"id={user.id}")


def care_topic_text(
    db: DBSession, rating: FeedbackRating, *, screenshot_links: bool = False,
) -> str:
    """Сообщение в служебный топик (О17): тариф, куратор, вид ОС и задание,
    оценка, комментарий. Обезличено: ученика в сообщении нет (владелец
    04.10.2026 — «везде, где отправлено, обезличено»), тариф остаётся — он
    человека не называет. Скриншоты обычно идут картинками рядом
    (`send_rating_to_care_topic`), ссылками — только запасным путём
    (`screenshot_links`). Ссылки на диалог нет: владелец 01.10.2026 — не нужна.
    Telegram разбирает HTML — всё пользовательское экранируется."""
    student = db.get(User, rating.student_id)
    curator = db.get(User, rating.curator_id) if rating.curator_id else None
    esc = html.escape
    lines = [
        f"<b>Оценка ОС: {rating.score} из {RATING_MAX}</b>",
        f"Тариф: {esc((student.tariff if student else None) or '—')}",
        f"Куратор: {esc(_person(curator))}",
        f"{esc(FEEDBACK_TYPE_LABELS.get(rating.feedback_type, rating.feedback_type))}: "
        f"{esc(rating.task_title or '—')}",
        "",
        esc(rating.comment),
    ]
    if screenshot_links and rating.images:
        links = ", ".join(
            f'<a href="{esc(image.image_s3_url)}">{index}</a>'
            for index, image in enumerate(rating.images, start=1)
        )
        lines += ["", f"Скриншоты: {links}"]
    return "\n".join(lines)


# Подпись к фото и альбому в Telegram — 0–1024 символа после разбора HTML.
CAPTION_LIMIT = 1024


def caption_length(text: str) -> int:
    """Длина подписи так, как её считает Telegram: без тегов, с раскрытыми
    `&lt;`, в единицах UTF-16 (эмодзи — две)."""
    visible = html.unescape(re.sub(r"<[^>]+>", "", text))
    return len(visible.encode("utf-16-le")) // 2


async def _download_screenshots(paths: list[str | None]) -> list[tuple[str, bytes]] | None:
    """Байты скриншотов из S3 в порядке оценки. Хоть один не скачался —
    None: лучше все ссылками, чем часть потерять молча. boto синхронный —
    в поток, как голосовое в `notify._telegram_voice`."""
    photos = []
    for path in paths:
        data = await asyncio.to_thread(s3_service.download_from_s3, path) if path else None
        if not data:
            return None
        photos.append((path.rsplit("/", 1)[-1], data))
    return photos


async def send_rating_to_care_topic(rating_id: int) -> None:
    """Фоновая задача после коммита оценки (О18, О24: каждая оценка — одна
    публикация в топике). Без `TELEGRAM_CARE_CHAT_ID` — пропуск с записью в
    лог; ошибка Telegram оценку не трогает: она уже сохранена.

    Варианты (владелец 01.10.2026):
    - без скриншотов — текстовое сообщение;
    - один — фото, оценка подписью; два-три — альбом, подпись у первого фото;
    - подпись длиннее `CAPTION_LIMIT` — фото без подписи, текст оценки следом
      ответом на первое из них;
    - фото не скачались или Telegram их не принял — текст со ссылками."""
    if not settings.telegram_care_chat_id:
        logger.info("feedback rating %s: TELEGRAM_CARE_CHAT_ID не задан, в топик не ушла", rating_id)
        return
    db = SessionLocal()
    try:
        rating = db.get(FeedbackRating, rating_id)
        if rating is None:
            return
        text = care_topic_text(db, rating)
        fallback = care_topic_text(db, rating, screenshot_links=True)
        paths = [image.image_s3_path for image in rating.images]
    except Exception:
        logger.exception("feedback rating %s: не собрали сообщение для топика", rating_id)
        return
    finally:
        db.close()

    chat_id = settings.telegram_care_chat_id
    thread_id = settings.telegram_care_thread_id or None
    if paths:
        photos = await _download_screenshots(paths)
        if photos:
            fits = caption_length(text) <= CAPTION_LIMIT
            caption = text if fits else ""
            if len(photos) == 1:
                filename, data = photos[0]
                first_id = await telegram_service.send_photo(
                    chat_id, data, filename=filename, caption=caption, message_thread_id=thread_id,
                )
            else:
                first_id = await telegram_service.send_media_group(
                    chat_id, photos, caption=caption, message_thread_id=thread_id,
                )
            if first_id is not None:
                if fits or await telegram_service.send_message(
                    chat_id, text, message_thread_id=thread_id, reply_to_message_id=first_id,
                ):
                    return
                logger.warning("feedback rating %s: фото в топике, текст оценки не ушёл", rating_id)
                return
        logger.warning("feedback rating %s: скриншоты картинками не ушли, шлём ссылками", rating_id)
        text = fallback

    sent = await telegram_service.send_message(chat_id, text, message_thread_id=thread_id)
    if not sent:
        logger.warning("feedback rating %s: Telegram не принял сообщение в топик", rating_id)


__all__ = [
    "DIALOG_MOCK_EXAM", "DIALOG_TASK_BLOCK", "RatingError", "care_topic_text",
    "close_dialog", "create_rating", "feedback_type_for_block", "get_rating",
    "has_staff_message", "is_closed", "parse_rating_input", "read_rating_form",
    "rate_request_notification", "rating_panel", "send_rating_to_care_topic",
    "serialize_rating",
]
