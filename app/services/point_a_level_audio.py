"""Голосовые файлы уровней точки А — по одной записи на уровень (1|2).

Экран загрузки — `app/api/cabinet_point_a_audio.py`. Само уведомление
(`app/services/point_a.py::maybe_notify_point_a_level`) читает отсюда
`get_level_audio`, ничего не загружает само.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from app.models.notification import Notification
from app.models.point_a_level_audio import PointALevelAudio
from app.services import s3 as s3_service

logger = logging.getLogger(__name__)


def get_level_audio(db: DBSession, level: int) -> PointALevelAudio | None:
    return db.query(PointALevelAudio).filter(PointALevelAudio.level == level).first()


def upsert_level_audio(
    db: DBSession, *, level: int, filename: str, data: bytes, content_type: str, uploaded_by_id: int,
) -> PointALevelAudio | None:
    """Загрузить голосовое для уровня в S3 и сохранить/обновить запись.

    Перезалив обновляет ту же строку — второй записи на уровень не
    появляется (`uq_point_a_level_audios_level`). Старый объект S3 удаляется,
    только если его не получил ни один ученик: `Notification.audio_url` хранит
    ссылку, и удаление оставило бы в уведомлениях немой плеер. Возвращает
    None, если загрузка в S3 не удалась (caller отвечает за ответ
    пользователю).

    `data` приходит уже перекодированным (`media_transcode.playable_voice` в
    роуте): имя и `content_type` — от m4a, ключ S3 берёт расширение из имени.
    """
    s3_path = s3_service.s3_path_point_a_level_audio(level, filename)
    s3_url = s3_service.upload_to_s3(s3_path, data, content_type or "audio/mpeg")
    if s3_service.is_configured() and not s3_url:
        logger.warning("point_a level audio upload failed for level=%s", level)
        return None

    existing = get_level_audio(db, level)
    old_path = existing.audio_s3_path if existing else None
    old_url = existing.audio_s3_url if existing else None

    if existing is None:
        existing = PointALevelAudio(level=level)
        db.add(existing)
    existing.audio_s3_path = s3_path
    existing.audio_s3_url = s3_url or ""
    existing.uploaded_by_id = uploaded_by_id
    existing.uploaded_at = datetime.now(timezone.utc)
    db.flush()

    if old_path and old_path != s3_path and not _sent_to_students(db, old_url):
        s3_service.delete_from_s3(old_path)

    return existing


def _sent_to_students(db: DBSession, audio_url: str | None) -> bool:
    if not audio_url:
        return False
    return db.query(Notification.id).filter(Notification.audio_url == audio_url).first() is not None
