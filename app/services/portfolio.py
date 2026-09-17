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
from app.models.work import WORK_TYPE_AFTER, WORK_TYPE_BEFORE, WORK_TYPE_MOCK_EXAM, Work
from app.services.program import msk_date
from app.services.utils import group_works

# Источник элемента галереи. Ушедшее наружу поле: экраны staff по нему
# решают, показывать ли удаление и перенос. У снимка сдачи `id` — это id
# строки `task_block_submission_images`, и для роутов работ он не значит
# ничего: отдать его туда — удалить или перенести чужой `Work`.
SOURCE_WORK = "work"
SOURCE_SUBMISSION = "submission"


def portfolio_item_count(db: DBSession, user_id: int) -> int:
    """Число современных элементов портфолио, которое совпадает с галереей.

    Одна строка `Work` считается одним элементом. У сдачи внутри задания
    галерея показывает каждый снимок отдельно, поэтому считаются изображения
    только завершённых сдач. Исторические `LegacyPortfolioPhoto` сюда не
    входят: у них отдельный read-only архив и отдельный счётчик.
    """
    work_count = (
        db.query(Work.id)
        .filter(
            Work.user_id == user_id,
            Work.work_type.in_((WORK_TYPE_BEFORE, WORK_TYPE_AFTER)),
            Work.status == "success",
        )
        .count()
    )
    submission_photo_count = (
        db.query(TaskBlockSubmissionImage.id)
        .join(
            TaskBlockSubmission,
            TaskBlockSubmission.id == TaskBlockSubmissionImage.submission_id,
        )
        .filter(
            TaskBlockSubmission.user_id == user_id,
            TaskBlockSubmission.submitted_at.isnot(None),
            TaskBlockSubmissionImage.image_s3_url != "",
        )
        .count()
    )
    return work_count + submission_photo_count


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


def student_portfolio_after_groups(
    db: DBSession,
    user_id: int,
    *,
    work_limit: int = 300,
    submission_limit: int = 300,
    mock_limit: int = 300,
) -> list[dict]:
    """То же, что `after_gallery_groups`, плюс оценённые финалы пробников —
    только для собственной страницы «Портфолио» ученика (владелец 14.09.2026:
    убрали отдельную вкладку «Пробные экзамены», «все сданные работы будут
    попадать в В процессе обучения»).

    Не расширяет саму `after_gallery_groups`: она общая со staff-карточкой
    ученика (`cabinet_students_shared.py`), у которой уже есть свой блок
    «Пробные экзамены» и массовое удаление месяца по `work_total` — заведи
    пробники туда же, staff получил бы возможность стереть оценённый финал
    через путь, который ничего не знает про `ExamCycle`/балл/этапы.

    Фильтр повторяет `cabinet_student.py::_collect_cycle_works(closed_only=True)`:
    только с выставленным баллом и не этапные (`parent_work_id IS NULL`) — это
    ровно то, что раньше показывала вкладка «Пробные экзамены».
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
    mock_works = (
        db.query(Work)
        .filter(
            Work.user_id == user_id,
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.status == "success",
            Work.score.isnot(None),
            Work.parent_work_id.is_(None),
        )
        .order_by(Work.created_at.desc())
        .limit(mock_limit)
        .all()
    )
    photos = _submission_photos(db, user_id, limit=submission_limit)
    return group_works(list(works) + mock_works + photos)


def item_source(item) -> str:
    """Источник элемента галереи: `Work` — «work», снимок сдачи — «submission»."""
    return getattr(item, "source", SOURCE_WORK)
