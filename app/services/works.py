"""Операции над работами ученика (`Work`), общие для staff и самого ученика.

Удаление переехало сюда из `api/cabinet_students_shared.py` (18.09.2026), когда
ученик получил право убирать свои фото портфолио, пока открыто окно загрузки:
две копии одного удаления разошлись бы на первом же изменении хранилища, а
файл в S3 и строку в базе нужно снимать одинаково, кто бы ни нажал кнопку.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DBSession

from app.models.work import Work
from app.services import s3 as s3_service


def delete_works_with_dependents(db: DBSession, works: list[Work]) -> int:
    """Удаляет работы вместе с этапными фото, в порядке, безопасном для FK.

    Этапные фото пробника ссылаются на финал через `parent_work_id`, поэтому
    сначала уходят они, потом сам финал. Коммит оставлен вызывающему: staff
    удаляет пачками, ученик по одной, и транзакцией управляет роут.
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

    ordered = sorted(by_id.values(), key=lambda w: 1 if w.is_final else 0)
    for work in ordered:
        if work.s3_path:
            s3_service.delete_from_s3(work.s3_path)
        db.delete(work)
    return len(ordered)
