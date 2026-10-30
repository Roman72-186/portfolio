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

Владелец 26.09.2026 добавил ещё два источника, тоже только на показ, поэтому
прежние сдачи видны сразу, без переноса: финальное фото домашнего задания
(`HomeworkSubmissionImage.is_final`) — везде, где показаны сдачи по заданиям;
финал отработки (`Work(retake)`, не этап) — только на странице самого ученика,
по той же причине, что и пробники (см. `student_portfolio_after_groups`).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session as DBSession

from app.constants import MONTHS
from app.models.homework_submission import HomeworkSubmission, HomeworkSubmissionImage
from app.models.exam_assignment import ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.role import Role
from app.models.task_block import TaskBlock, TaskBlockSubmission, TaskBlockSubmissionImage
from app.models.tracker import TrackerTask
from app.models.user import User
from app.models.work import (
    WORK_TYPE_AFTER,
    WORK_TYPE_BEFORE,
    WORK_TYPE_MOCK_EXAM,
    WORK_TYPE_RETAKE,
    Work,
)
from app.services.program import msk_date
from app.services.exam_cycle import get_score_neighbor_candidates, pick_score_neighbors
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
    homework_photo_count = _homework_final_images_query(db, user_id).count()
    return work_count + submission_photo_count + homework_photo_count


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
    task_submission_id: int | None = None


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
            task_submission_id=submission.id,
        ))
    return photos


def _homework_final_images_query(db: DBSession, user_id: int):
    """Финальные фото сданных домашних заданий ученика.

    Только финал: промежуточные снимки — ход работы, а не результат. Сдача,
    возвращённая на доработку, остаётся в показе — её финал перезаписывается
    на месте при пересдаче (`HomeworkSubmission`).
    """
    return (
        db.query(HomeworkSubmissionImage, HomeworkSubmission)
        .join(
            HomeworkSubmission,
            HomeworkSubmission.id == HomeworkSubmissionImage.submission_id,
        )
        .filter(
            HomeworkSubmission.user_id == user_id,
            HomeworkSubmission.submitted_at.isnot(None),
            HomeworkSubmissionImage.is_final.is_(True),
            HomeworkSubmissionImage.image_s3_url != "",
        )
    )


def _homework_photos(db: DBSession, user_id: int, *, limit: int) -> list[SubmissionPhoto]:
    """Финалы домашних заданий в виде снимков сдачи, свежие первыми.

    Источник тот же, `SOURCE_SUBMISSION`: `id` здесь — строка
    `homework_submission_images`, и роутам работ его отдавать нельзя.
    """
    rows = (
        _homework_final_images_query(db, user_id)
        .order_by(HomeworkSubmission.submitted_at.desc(), HomeworkSubmissionImage.id)
        .limit(limit)
        .all()
    )
    photos = []
    for image, submission in rows:
        day = msk_date(submission.submitted_at)
        photos.append(SubmissionPhoto(
            id=image.id,
            filename=f"Домашнее задание {day.strftime('%d.%m.%Y')}",
            s3_url=image.image_s3_url,
            year=day.year,
            month=MONTHS[day.month - 1],
            created_at=submission.submitted_at,
        ))
    return photos


def _after_items(db: DBSession, user_id: int, *, work_limit: int, submission_limit: int) -> list:
    """Общая часть «После»: `Work(after)`, сдачи в заданиях, финалы домашек."""
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
    return (
        list(works)
        + _submission_photos(db, user_id, limit=submission_limit)
        + _homework_photos(db, user_id, limit=submission_limit)
    )


def after_gallery_groups(
    db: DBSession,
    user_id: int,
    *,
    work_limit: int = 300,
    submission_limit: int = 300,
) -> list[dict]:
    """Раздел «После» по месяцам: `Work(after)` вместе со сдачами в заданиях
    и финалами домашних заданий.

    Возвращает то же, что `group_works`, плюс `work_total` в каждой группе —
    сколько там настоящих `Work`. По нему экраны staff считают массовое
    удаление месяца: общий `total` завысил бы число удаляемого, потому что
    сдачи по заданиям и домашки этот роут не трогает.
    """
    groups = group_works(
        _after_items(db, user_id, work_limit=work_limit, submission_limit=submission_limit)
    )
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
    include_score_comparisons: bool = False,
) -> list[dict]:
    """То же, что `after_gallery_groups`, плюс оценённые финалы пробников и
    финалы отработок — только для собственной страницы «Портфолио» ученика
    (владелец 14.09.2026: убрали отдельную вкладку «Пробные экзамены», «все
    сданные работы будут попадать в В процессе обучения»; отработки добавлены
    26.09.2026: «финальное фото должно загружаться»).

    Не расширяет саму `after_gallery_groups`: она общая со staff-карточкой
    ученика (`cabinet_students_shared.py`), у которой уже есть свои блоки
    «Пробные экзамены» и «Отработки» и массовое удаление месяца по
    `work_total` — заведи их туда же, staff получил бы возможность стереть
    финал через путь, который ничего не знает про `ExamCycle`/балл/этапы.

    Фильтр пробников повторяет `cabinet_student.py::_collect_cycle_works(closed_only=True)`:
    только с выставленным баллом и не этапные (`parent_work_id IS NULL`) — это
    ровно то, что раньше показывала вкладка «Пробные экзамены». У отработки
    балл не требуется: ученик сам указывает свой балл при загрузке, финал
    показывается сразу. Промежуточные фото отработки (`is_final=False`) не идут.
    """
    items = _after_items(db, user_id, work_limit=work_limit, submission_limit=submission_limit)
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
    retake_finals = (
        db.query(Work)
        .filter(
            Work.user_id == user_id,
            Work.work_type == WORK_TYPE_RETAKE,
            Work.status == "success",
            Work.is_final.is_(True),
            Work.parent_work_id.is_(None),
        )
        .order_by(Work.created_at.desc())
        .limit(mock_limit)
        .all()
    )
    groups = group_works(items + mock_works + retake_finals)
    if include_score_comparisons:
        _attach_score_comparisons(db, user_id, groups)
    return groups


def _peer_name(user: User) -> str:
    """Имя и первая буква фамилии для ученического сравнения баллов."""
    parts = (user.name or "").split()
    first = (user.first_name or "").strip() or (parts[0] if parts else "Ученик")
    last = (user.last_name or "").strip() or (parts[-1] if len(parts) > 1 else "")
    return f"{first} {last[0]}." if last else first


def _attach_score_comparisons(db: DBSession, user_id: int, groups: list[dict]) -> None:
    """Соседние оценки по тому же билету или блоку сдачи, одним батчем на страницу.

    В портфолио фотографии одной сдачи идут отдельными плитками, а сравнение
    относится к работе целиком. Поэтому ниже каждая сдача появляется один раз.
    """
    mock_works: list[Work] = []
    submission_ids: set[int] = set()
    for group in groups:
        for item in group["works"]:
            if isinstance(item, Work) and item.work_type == WORK_TYPE_MOCK_EXAM and item.score is not None:
                mock_works.append(item)
            elif isinstance(item, SubmissionPhoto) and item.task_submission_id is not None:
                submission_ids.add(item.task_submission_id)

    own_submissions = {}
    if submission_ids:
        own_submissions = {
            row.id: (row, task.title)
            for row, task in (
                db.query(TaskBlockSubmission, TrackerTask)
                .join(TaskBlock, TaskBlockSubmission.block_id == TaskBlock.id)
                .join(TrackerTask, TaskBlock.task_id == TrackerTask.id)
                .filter(
                    TaskBlockSubmission.id.in_(submission_ids),
                    TaskBlockSubmission.user_id == user_id,
                    TaskBlockSubmission.submitted_at.isnot(None),
                    TaskBlockSubmission.score.isnot(None),
                    TaskBlockSubmission.needs_revision.is_(False),
                )
                .all()
            )
        }

    cycle_ids = {work.cycle_id for work in mock_works if work.cycle_id}
    cycles = (
        {cycle.id: cycle for cycle in db.query(ExamCycle).filter(ExamCycle.id.in_(cycle_ids)).all()}
        if cycle_ids else {}
    )
    ticket_ids = {cycle.ticket_id for cycle in cycles.values() if cycle.ticket_id}
    ticket_titles = (
        {ticket.id: ticket.title for ticket in db.query(ExamTicket).filter(ExamTicket.id.in_(ticket_ids)).all()}
        if ticket_ids else {}
    )
    mock_candidates = get_score_neighbor_candidates(
        db, exclude_user_id=user_id, work_type=WORK_TYPE_MOCK_EXAM,
        ticket_ids=list(ticket_ids),
    )

    block_ids = {row.block_id for row, _title in own_submissions.values()}
    block_candidates: dict[int, list[tuple[float, int]]] = {}
    if block_ids:
        rows = (
            db.query(TaskBlockSubmission.score, TaskBlockSubmission.id, TaskBlockSubmission.block_id)
            .join(User, TaskBlockSubmission.user_id == User.id)
            .join(Role, User.role_id == Role.id)
            .filter(
                TaskBlockSubmission.block_id.in_(block_ids),
                TaskBlockSubmission.user_id != user_id,
                TaskBlockSubmission.submitted_at.isnot(None),
                TaskBlockSubmission.score.isnot(None),
                TaskBlockSubmission.needs_revision.is_(False),
                User.deleted_at.is_(None),
                Role.name == "ученик",
            )
            .all()
        )
        for score, submission_id, block_id in rows:
            block_candidates.setdefault(block_id, []).append((float(score), submission_id))

    selected_mock: set[int] = set()
    selected_submissions: set[int] = set()
    picked_by_work: dict[int, dict] = {}
    picked_by_submission: dict[int, dict] = {}
    for work in mock_works:
        cycle = cycles.get(work.cycle_id)
        if cycle and cycle.ticket_id:
            picked = pick_score_neighbors(float(work.score), mock_candidates.get(cycle.ticket_id, []))
            picked_by_work[work.id] = picked
            selected_mock.update(peer_id for side in picked.values() for _score, peer_id in side)
    for submission, _title in own_submissions.values():
        picked = pick_score_neighbors(float(submission.score), block_candidates.get(submission.block_id, []))
        picked_by_submission[submission.id] = picked
        selected_submissions.update(peer_id for side in picked.values() for _score, peer_id in side)

    peer_names: dict[tuple[str, int], str] = {}
    if selected_mock:
        for work, student in (
            db.query(Work, User).join(User, Work.user_id == User.id)
            .filter(Work.id.in_(selected_mock)).all()
        ):
            peer_names[("mock", work.id)] = _peer_name(student)
    if selected_submissions:
        for submission, student in (
            db.query(TaskBlockSubmission, User)
            .join(User, TaskBlockSubmission.user_id == User.id)
            .filter(TaskBlockSubmission.id.in_(selected_submissions)).all()
        ):
            peer_names[("submission", submission.id)] = _peer_name(student)

    def neighbors(picked: dict, kind: str) -> dict[str, list[dict]]:
        return {
            side: [
                {"name": peer_names[(kind, peer_id)], "score": score}
                for score, peer_id in (reversed(picked[side]) if side == "below" else picked[side])
                if (kind, peer_id) in peer_names
            ]
            for side in ("above", "below")
        }

    for group in groups:
        comparisons: list[dict] = []
        seen_submissions: set[int] = set()
        for item in group["works"]:
            if isinstance(item, Work) and item.id in picked_by_work:
                cycle = cycles[item.cycle_id]
                comparisons.append({
                    "title": ticket_titles.get(cycle.ticket_id, "Пробный экзамен"),
                    "score": float(item.score),
                    **neighbors(picked_by_work[item.id], "mock"),
                })
            elif isinstance(item, SubmissionPhoto) and item.task_submission_id in own_submissions:
                submission_id = item.task_submission_id
                if submission_id in seen_submissions:
                    continue
                seen_submissions.add(submission_id)
                submission, title = own_submissions[submission_id]
                comparisons.append({
                    "title": title,
                    "score": float(submission.score),
                    **neighbors(picked_by_submission[submission_id], "submission"),
                })
        group["score_comparisons"] = comparisons


def item_source(item) -> str:
    """Источник элемента галереи: `Work` — «work», снимок сдачи — «submission»."""
    return getattr(item, "source", SOURCE_WORK)
