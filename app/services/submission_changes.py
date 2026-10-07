"""История правок уже сданной работы в блоке задания.

Владелец 07.10.2026: «нужно фиксировать сколько и когда были замены фото в
заданиях»; показывать — в карточке проверки, вместе с прежними фото («было —
стало»), считать с дня выкатки, прошлое не восстанавливать.

До этого замена стирала строки старых фото, а `mark_submitted` переписывал
`submitted_at` на время замены: по базе нельзя было сказать ни сколько раз
ученик менял работу, ни что было раньше. Файлы в S3 при этом оставались —
здесь сохраняется только ссылка на них.

Пишут три роута правки сдачи в `api/cabinet_tracker.py` — загрузка (замена
или догрузка), удаление фото. Своих проверок прав здесь нет: правку к этому
моменту уже пропустил `submission_edit.block_work_reason`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session as DBSession

from app.models.task_block import (
    CHANGE_ADD,
    CHANGE_DELETE,
    CHANGE_REPLACE,
    TaskBlockState,
    TaskBlockSubmission,
    TaskBlockSubmissionChange,
    TaskBlockSubmissionChangeImage,
    TaskBlockSubmissionImage,
)

CHANGE_LABELS = {
    CHANGE_REPLACE: "замена фото",
    CHANGE_DELETE: "удаление фото",
    CHANGE_ADD: "догрузка фото",
}


# Фото работы как пара «ссылка, путь в S3» — снимок, а не строка таблицы.
PhotoRef = tuple[str, str | None]


def photo_snapshot(db: DBSession, submission_id: int) -> list[PhotoRef]:
    """Фото сдачи до правки — значениями, без загрузки объектов в сессию.

    Замена сносит строки фото массовым `delete` в обход сессии, и объекты,
    загруженные до него, остались бы в ней висеть: SQLite в тестах выдаёт
    новым фото те же id, и SQLAlchemy ругается на подмену в identity map.
    """
    return [
        (url, path) for url, path in
        db.query(TaskBlockSubmissionImage.image_s3_url, TaskBlockSubmissionImage.image_s3_path)
        .filter(TaskBlockSubmissionImage.submission_id == submission_id)
        .order_by(TaskBlockSubmissionImage.sort_order, TaskBlockSubmissionImage.id)
        .all()
    ]


def record_change(
    db: DBSession, *, submission: TaskBlockSubmission, kind: str,
    photos_before: int, photos_after: int,
    removed: list[PhotoRef] | tuple = (),
    was_submitted: bool,
) -> TaskBlockSubmissionChange | None:
    """Записать правку, если работа до неё была сдана.

    `was_submitted` вызывающий снимает **до** правки: загрузка сама зовёт
    `mark_submitted`, и после неё `submitted_at` стоит всегда. Неполная
    сдача «2 из 3» ещё не сдана — её догрузка правкой не считается.
    Транзакцию не коммитит: запись едет одним коммитом с самой правкой.
    """
    if not was_submitted:
        return None
    change = TaskBlockSubmissionChange(
        submission_id=submission.id, kind=kind,
        photos_before=photos_before, photos_after=photos_after,
    )
    db.add(change)
    db.flush()
    for order, (url, path) in enumerate(removed):
        db.add(TaskBlockSubmissionChangeImage(
            change_id=change.id, image_s3_url=url, image_s3_path=path, sort_order=order,
        ))
    db.flush()
    return change


def change_history(db: DBSession, submission: TaskBlockSubmission) -> dict | None:
    """История для карточки проверки; `None` — работу после сдачи не меняли.

    Первая сдача — отметка блока (`completed_at`): `submitted_at` правка
    переписывает. Отметки нет (блок закрыли иначе) — момент самой ранней
    правки неизвестен, тогда строку о первой сдаче не показываем.
    """
    changes = (
        db.query(TaskBlockSubmissionChange)
        .filter(TaskBlockSubmissionChange.submission_id == submission.id)
        .order_by(TaskBlockSubmissionChange.created_at, TaskBlockSubmissionChange.id)
        .all()
    )
    if not changes:
        return None
    images: dict[int, list[TaskBlockSubmissionChangeImage]] = {}
    for image in (
        db.query(TaskBlockSubmissionChangeImage)
        .filter(TaskBlockSubmissionChangeImage.change_id.in_([c.id for c in changes]))
        .order_by(TaskBlockSubmissionChangeImage.sort_order, TaskBlockSubmissionChangeImage.id)
    ):
        images.setdefault(image.change_id, []).append(image)
    state = db.query(TaskBlockState).filter(
        TaskBlockState.block_id == submission.block_id,
        TaskBlockState.user_id == submission.user_id,
    ).one_or_none()
    first: datetime | None = state.completed_at if state else None
    return {
        "first_submitted_at": first,
        "count": len(changes),
        "changes": [
            {
                "id": change.id,
                "label": CHANGE_LABELS.get(change.kind, change.kind),
                "created_at": change.created_at,
                "photos_before": change.photos_before,
                "photos_after": change.photos_after,
                "removed": images.get(change.id, []),
            }
            for change in changes
        ],
    }
