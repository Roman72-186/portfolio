"""Галерея «После обучения»: работы портфолио плюс работы, сданные в заданиях.

Владелец 09.09.2026: «все остальные работы в ПОСЛЕ попадают, когда
пользователь сдает в заданиях». До этого раздел «После» показывал только
`Work(after)` — то, что ученик загрузил на общем экране `/upload`, — а всё
сданное внутри заданий лежало в `TaskBlockSubmission` и в портфолио не
попадало вовсе.

Слияние только на показ: в базе ничего не копируется. `TaskBlockSubmission`
остаётся единственным хранилищем сдач по заданиям (причины — в докстринге
`app/models/task_block.py::TaskBlockSubmission`), а вторая копия в `Work`
разошлась бы с ним при первой же пересдаче.

Группировку по месяцам делает существующий `services/utils.py::group_works`,
свой второй группировщик здесь не заводится: снимок сдачи заворачивается в
лёгкий объект с теми же полями, которые читает `group_works`.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session as DBSession

from app.constants import MONTHS
from app.models.task_block import TaskBlockSubmission, TaskBlockSubmissionImage
from app.models.work import WORK_TYPE_AFTER, Work
from app.services.program import msk_date
from app.services.utils import group_works

# Источник элемента галереи. Ушедшее наружу поле: экраны staff по нему
# решают, показывать ли удаление и перенос. У снимка сдачи `id` — это id
# строки `task_block_submission_images`, и для роутов работ он не значит
# ничего: отдать его туда — удалить или перенести чужой `Work`.
SOURCE_WORK = "work"
SOURCE_SUBMISSION = "submission"


@dataclass(frozen=True)
class SubmissionPhoto:
    """Снимок сдачи в виде, который понимает `group_works`.

    Поля повторяют те, что группировщик читает у `Work`: `year`, `month`
    (русское название месяца), `created_at`, `score`. `score` всегда пуст —
    работы по заданиям оценивает куратор в своей карточке проверки, а не
    средним баллом месяца в портфолио.
    """

    id: int
    filename: str
    s3_url: str
    year: int
    month: str
    created_at: datetime
    score: None = None
    drive_file_id: None = None
    source: str = SOURCE_SUBMISSION


def _submission_photos(db: DBSession, user_id: int, *, limit: int) -> list[SubmissionPhoto]:
    """Снимки сданных работ ученика, свежие первыми.

    Незаконченная сдача (строка заведена первой загрузкой, `submitted_at`
    пуст) в галерею не идёт: с точки зрения ученика работа ещё не сдана.
    """
    rows = (
        db.query(TaskBlockSubmissionImage, TaskBlockSubmission)
        .join(
            TaskBlockSubmission,
            TaskBlockSubmission.id == TaskBlockSubmissionImage.submission_id,
        )
        .filter(
            TaskBlockSubmission.user_id == user_id,
            TaskBlockSubmission.submitted_at.isnot(None),
            TaskBlockSubmissionImage.image_s3_url != "",
        )
        .order_by(TaskBlockSubmission.submitted_at.desc(), TaskBlockSubmissionImage.id)
        .limit(limit)
        .all()
    )
    photos = []
    for image, submission in rows:
        day = msk_date(submission.submitted_at)
        photos.append(SubmissionPhoto(
            id=image.id,
            filename=f"Работа по заданию {day.strftime('%d.%m.%Y')}",
            s3_url=image.image_s3_url,
            year=day.year,
            month=MONTHS[day.month - 1],
            created_at=submission.submitted_at,
        ))
    return photos


def after_gallery_groups(
    db: DBSession,
    user_id: int,
    *,
    work_limit: int = 300,
    submission_limit: int = 300,
) -> list[dict]:
    """Раздел «После» по месяцам: `Work(after)` вместе со сдачами в заданиях.

    Возвращает то же, что `group_works`, плюс `work_total` в каждой группе —
    сколько там настоящих `Work`. По нему экраны staff считают массовое
    удаление месяца: общий `total` завысил бы число удаляемого, потому что
    сдачи по заданиям этот роут не трогает.
    """
    works = (
        db.query(Work)
        .filter(
            Work.user_id == user_id,
            Work.work_type == WORK_TYPE_AFTER,
            Work.status == "success",
        )
        .order_by(Work.created_at.desc())
        .limit(work_limit)
        .all()
    )
    photos = _submission_photos(db, user_id, limit=submission_limit)

    groups = group_works(list(works) + photos)
    for group in groups:
        group["work_total"] = sum(
            1 for item in group["works"] if getattr(item, "source", SOURCE_WORK) == SOURCE_WORK
        )
    return groups


def item_source(item) -> str:
    """Источник элемента галереи: `Work` — «work», снимок сдачи — «submission»."""
    return getattr(item, "source", SOURCE_WORK)
