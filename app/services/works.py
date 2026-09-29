"""Операции над работами ученика (`Work`), общие для staff и самого ученика.

Удаление переехало сюда из `api/cabinet_students_shared.py` (18.09.2026), когда
ученик получил право убирать свои фото портфолио, пока открыто окно загрузки:
две копии одного удаления разошлись бы на первом же изменении хранилища, а
файл в S3 и строку в базе нужно снимать одинаково, кто бы ни нажал кнопку.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DBSession

from app.models.feedback import Feedback
from app.models.work import Work
from app.services import s3 as s3_service
from app.services.utils import compress_image

# Превью для квадратиков карточки ученика: 84–88px на экране, на айфоне это
# ~260 физических пикселей. Больше не нужно, меньше — видно мыло.
THUMB_MAX_PX = 320
THUMB_QUALITY = 78


class WorkHasFeedbackError(Exception):
    """По работе уже идёт диалог обратной связи — удалять её нельзя."""


WORK_HAS_FEEDBACK = (
    "Нельзя удалить: по работе уже есть обратная связь куратора. "
    "Удаление стёрло бы файл, на который ссылается диалог."
)


def upload_work_thumb(s3_path: str, image_bytes: bytes) -> str | None:
    """Кладёт в S3 превью работы и возвращает его ссылку.

    Синхронная — зовётся в executor рядом с загрузкой самого фото. Получает уже
    сжатые 1600px, чтобы не декодировать исходник второй раз. Сбой превью не
    роняет загрузку: `None` в `Work.thumb_s3_url`, и экран покажет само фото.
    """
    thumb = compress_image(image_bytes, max_px=THUMB_MAX_PX, quality=THUMB_QUALITY)
    return s3_service.upload_to_s3(s3_service.s3_path_thumb(s3_path), thumb, "image/jpeg")


def delete_works_with_dependents(db: DBSession, works: list[Work]) -> int:
    """Удаляет работы вместе с этапными фото, в порядке, безопасном для FK.

    Этапные фото пробника ссылаются на финал через `parent_work_id`, поэтому
    сначала уходят они, потом сам финал. Коммит оставлен вызывающему: staff
    удаляет пачками, ученик по одной, и транзакцией управляет роут.

    Если хоть по одной работе есть обратная связь — `WorkHasFeedbackError`,
    и ничего не удаляется: ни строки, ни файлы.
    """
    by_id = {w.id: w for w in works}
    final_ids = [w.id for w in works if w.is_final]
    if final_ids:
        for child in (
            db.query(Work)
            .filter(Work.parent_work_id.in_(final_ids))
            .all()
        ):
            by_id[child.id] = child

    # Код-ревью 28.09.2026, P1: `feedbacks.work_id` — ключ без `ondelete`.
    # Без этой проверки файл в S3 уходил сразу, коммит роута падал на ключе,
    # строка откатывалась — и в диалоге оставалась битая картинка. Проверка
    # до первого удаления, для всех работ пачки: S3 не откатывается.
    if db.query(Feedback.id).filter(Feedback.work_id.in_(list(by_id))).first():
        raise WorkHasFeedbackError(WORK_HAS_FEEDBACK)

    ordered = sorted(by_id.values(), key=lambda w: 1 if w.is_final else 0)
    for work in ordered:
        if work.s3_path:
            s3_service.delete_from_s3(work.s3_path)
            s3_service.delete_from_s3(s3_service.s3_path_thumb(work.s3_path))
        db.delete(work)
    return len(ordered)
