"""Поворот загруженного фото на 90° и право на него.

До 04.10.2026 поворот был только у суперадмина. Владелец открыл его всем
ролям, но каждой в своей зоне: ученик крутит свои работы, куратор — работы
своих учеников, Главный преподаватель и суперадмин — любое фото, включая
билеты и картинки заданий. Модератор только смотрит.

Поворот деструктивный: файл в S3 перезаписывается тем же ключом, поэтому
право решается по конкретному файлу, а не по рангу. Хозяина ищем по таблицам,
где лежит путь в S3; файл, которого нет ни в одной из них, — контент школы
(билеты, картинки блоков и домашек) или неизвестный объект, и его трогает
только уровень Главного преподавателя.

Правило одно на просмотрщик и на сам поворот: `can_rotate` зовут и
`POST /cabinet/rotate-photo`, и проверка кнопок `/cabinet/rotate-photo/allowed`.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session as DBSession

from app.models.feedback import Feedback, FeedbackMessage, FeedbackPhoto
from app.models.feedback_rating import FeedbackRating, FeedbackRatingImage
from app.models.homework_feedback import HomeworkFeedback, HomeworkFeedbackMessage
from app.models.homework_submission import HomeworkSubmission, HomeworkSubmissionImage
from app.models.legacy_portfolio_photo import LegacyPortfolioPhoto
from app.models.task_block import TaskBlockSubmission, TaskBlockSubmissionImage
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.models.user import User
from app.models.work import Work
from app.services import s3 as s3_service
from app.services.rbac import MODERATOR_ROLE_NAME
from app.services.student_access import get_student_for_staff_access
from app.services.utils import rotate_image_bytes
from app.services.works import upload_work_thumb

# С этого ранга (Главный преподаватель) поворот открыт для любого файла бакета.
FULL_ACCESS_RANK = 4


@dataclass(frozen=True)
class PhotoOwner:
    """Чьё фото. `sender_id` задан у фото из переписки: ученик крутит в
    диалоге только то, что прислал сам, а не скриншот куратора."""

    student_id: int
    sender_id: int | None = None


def s3_path_from_src(src: str) -> str | None:
    """Ключ в S3 по ссылке из `<img src>`.

    Срезает cache-busting `?v=…` (повторный поворот шлёт уже изменённый URL) и
    раскодирует percent-encoding: браузер отдаёт кириллицу путей (тариф, папки
    «До»/«После») закодированной, а ключ в S3 — сырой. Ссылка не из нашего
    бакета даёт `None`.
    """
    clean_url = (src or "").split("?", 1)[0]
    s3_path = s3_service.s3_path_from_public_url(clean_url)
    if not s3_path:
        return None
    return urllib.parse.unquote(s3_path)


def resolve_photo_owner(db: DBSession, s3_path: str) -> PhotoOwner | None:
    """Ученик, которому принадлежит файл, или `None` для контента школы."""
    work = db.query(Work.user_id).filter(Work.s3_path == s3_path).first()
    if work:
        return PhotoOwner(student_id=work.user_id)

    legacy = (
        db.query(LegacyPortfolioPhoto.user_id)
        .filter(LegacyPortfolioPhoto.s3_path == s3_path)
        .first()
    )
    if legacy:
        return PhotoOwner(student_id=legacy.user_id)

    row = (
        db.query(TaskBlockSubmission.user_id)
        .join(TaskBlockSubmissionImage, TaskBlockSubmissionImage.submission_id == TaskBlockSubmission.id)
        .filter(TaskBlockSubmissionImage.image_s3_path == s3_path)
        .first()
    )
    if row:
        return PhotoOwner(student_id=row.user_id)

    row = (
        db.query(HomeworkSubmission.user_id)
        .join(HomeworkSubmissionImage, HomeworkSubmissionImage.submission_id == HomeworkSubmission.id)
        .filter(HomeworkSubmissionImage.image_s3_path == s3_path)
        .first()
    )
    if row:
        return PhotoOwner(student_id=row.user_id)

    row = (
        db.query(FeedbackRating.student_id)
        .join(FeedbackRatingImage, FeedbackRatingImage.rating_id == FeedbackRating.id)
        .filter(FeedbackRatingImage.image_s3_path == s3_path)
        .first()
    )
    if row:
        return PhotoOwner(student_id=row.student_id)

    # Переписка обратной связи: ученик диалога плюс автор фото.
    row = (
        db.query(Work.user_id, FeedbackMessage.sender_id)
        .join(Feedback, Feedback.work_id == Work.id)
        .join(FeedbackMessage, FeedbackMessage.feedback_id == Feedback.id)
        .filter(FeedbackMessage.photo_s3_path == s3_path)
        .first()
    )
    if row:
        return PhotoOwner(student_id=row.user_id, sender_id=row.sender_id)

    # Старые фото куратора к разбору (до сообщений) — автор куратор диалога.
    row = (
        db.query(Work.user_id, Feedback.curator_id)
        .join(Feedback, Feedback.work_id == Work.id)
        .join(FeedbackPhoto, FeedbackPhoto.feedback_id == Feedback.id)
        .filter(FeedbackPhoto.s3_path == s3_path)
        .first()
    )
    if row:
        return PhotoOwner(student_id=row.user_id, sender_id=row.curator_id)

    row = (
        db.query(HomeworkSubmission.user_id, HomeworkFeedbackMessage.sender_id)
        .join(HomeworkFeedback, HomeworkFeedback.submission_id == HomeworkSubmission.id)
        .join(HomeworkFeedbackMessage, HomeworkFeedbackMessage.feedback_id == HomeworkFeedback.id)
        .filter(HomeworkFeedbackMessage.photo_s3_path == s3_path)
        .first()
    )
    if row:
        return PhotoOwner(student_id=row.user_id, sender_id=row.sender_id)

    row = (
        db.query(TaskBlockSubmission.user_id, TaskBlockFeedbackMessage.sender_id)
        .join(TaskBlockFeedback, TaskBlockFeedback.submission_id == TaskBlockSubmission.id)
        .join(TaskBlockFeedbackMessage, TaskBlockFeedbackMessage.feedback_id == TaskBlockFeedback.id)
        .filter(TaskBlockFeedbackMessage.photo_s3_path == s3_path)
        .first()
    )
    if row:
        return PhotoOwner(student_id=row.user_id, sender_id=row.sender_id)

    return None


def can_rotate(db: DBSession, user: dict, s3_path: str) -> bool:
    """Может ли `user` повернуть файл `s3_path`.

    Модератора отсекаем явно: его `role_rank` равен 4, и без этой строки
    проверка кнопок отдала бы ему всё. Сам POST модератору закрывает ещё и
    общий гейт в `dependencies.py`.
    """
    if user.get("role_name") == MODERATOR_ROLE_NAME:
        return False
    rank = user["role_rank"]
    if rank >= FULL_ACCESS_RANK:
        return True

    owner = resolve_photo_owner(db, s3_path)
    if owner is None:
        return False

    if rank == 1:
        if owner.student_id != user["user_id"]:
            return False
        if owner.sender_id is not None and owner.sender_id != user["user_id"]:
            return False
        student = db.get(User, owner.student_id)
        return student is not None and student.archived_at is None

    # Куратор: только действующие свои ученики, архив — на чтение.
    try:
        get_student_for_staff_access(
            db,
            user,
            owner.student_id,
            active_only=True,
            exclude_deleted=True,
            not_found_detail="",
            forbidden_detail="",
        )
    except HTTPException:
        return False
    return True


@dataclass(frozen=True)
class RotateResult:
    url: str | None = None
    thumb_url: str | None = None
    error: str | None = None


def work_with_thumb(db: DBSession, s3_path: str) -> Work | None:
    """Работа, у которой есть превью: его надо пересобрать вместе с фото."""
    return (
        db.query(Work)
        .filter(Work.s3_path == s3_path, Work.thumb_s3_url.isnot(None))
        .first()
    )


def rotate_in_storage(s3_path: str, *, clockwise: bool, rebuild_thumb: bool) -> RotateResult:
    """Скачивает файл, крутит на полном разрешении и перезаписывает тот же ключ.

    `s3_url`/`s3_path` не меняются, поэтому записи в базе трогать не нужно —
    кроме превью работы (с 29.09.2026): его пересобирают из повёрнутого снимка
    (`rebuild_thumb`), а `?v=` в `thumb_s3_url` пишет вызывающий. Видео
    (отчёты кураторов) PIL не откроет — это чистая ошибка. Только сеть и PIL,
    без базы: роут гоняет функцию в пуле потоков.
    """
    data = s3_service.download_from_s3(s3_path)
    if data is None:
        return RotateResult(error="Не удалось загрузить файл из хранилища")
    try:
        rotated = rotate_image_bytes(data, clockwise=clockwise)
    except Exception:  # noqa: BLE001 — PIL не открыл (видео/битый файл)
        return RotateResult(error="Это не изображение — поворот недоступен")
    new_url = s3_service.upload_to_s3(s3_path, rotated, "image/jpeg")
    if not new_url:
        return RotateResult(error="Не удалось сохранить повёрнутое фото")
    thumb_url = upload_work_thumb(s3_path, rotated) if rebuild_thumb else None
    return RotateResult(url=new_url, thumb_url=thumb_url)
