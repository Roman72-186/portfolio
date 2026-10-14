"""Общая обратная связь на несколько заданий (созвон 13.10.2026).

Ученики неделями сдают работы без ответа. Куратор отвечает один раз на все:
преподаватель на блоке сдачи «главного» задания отмечает блоки сдачи прошлых
заданий (`TaskBlockFeedbackLink`). Ученики сдают как раньше. Куратор
открывает сдачу главного блока, видит рядом все связанные работы ученика —
дату сдачи, цикл, название задания — и пишет один ответ. Ученик видит этот
ответ по каждой из работ.

Здесь все правила связки, своих копий у роутов и шаблонов нет:

- **«Поглощённая» сдача** — сдача связанного блока, у которой нет своего
  диалога ОС. Своего диалога нет, пока куратор по ней не писал, поэтому
  связка задним числом не отбирает у работы уже данный ей ответ: такая
  работа остаётся со своим диалогом и в общую ОС не входит.
- Поглощённая сдача **не идёт в очередь куратора отдельной строкой**
  (владелец 14.10.2026: «только общей строкой»). Пока главная работа не
  сдана, строки нет вовсе; сдана — одна строка главной со всеми работами.
- Ответ, отметка «проверено» и «Завершить ОС» живут на главной сдаче; балл и
  возврат на доработку — по каждой работе, как раньше.
- Ответ куратора в общей ОС запирает правку и поглощённых работ
  (`submission_edit`), иначе ученик поменял бы фото после ответа.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from sqlalchemy.orm import Session as DBSession

from app.models.learning_topic import LearningTopic
from app.models.task_block import (
    BLOCK_TYPE_LABELS,
    DIALOG_BLOCK_TYPES,
    TaskBlock,
    TaskBlockFeedbackLink,
    TaskBlockSubmission,
    TaskBlockSubmissionImage,
)
from app.models.task_block_feedback import TaskBlockFeedback
from app.models.tracker import TrackerTask
from app.services.tz import msk_date_text

# Сколько прошлых блоков можно связать с одним главным. С запасом: на созвоне
# говорили «три, четыре, пять».
MAX_LINKED_BLOCKS = 20


class LinkError(ValueError):
    """Связку нельзя сохранить. `ValueError` — роуты конструктора отвечают на
    него 422 с текстом для преподавателя."""


# --- связка блоков ----------------------------------------------------------

def linked_block_ids(db: DBSession, main_block_ids: list[int]) -> dict[int, list[int]]:
    """Главный блок → связанные блоки, для формы правки."""
    if not main_block_ids:
        return {}
    grouped: dict[int, list[int]] = {}
    for row in (
        db.query(TaskBlockFeedbackLink)
        .filter(TaskBlockFeedbackLink.block_id.in_(main_block_ids))
        .order_by(TaskBlockFeedbackLink.linked_block_id)
    ):
        grouped.setdefault(row.block_id, []).append(row.linked_block_id)
    return grouped


def main_block_ids(db: DBSession, block_ids: set[int] | list[int]) -> dict[int, int]:
    """Связанный блок → его главный.

    Главный в удалённом задании связи не держит: иначе прошлые работы
    навсегда пропали бы из очереди куратора, ожидая сдачи, которой не будет.
    """
    if not block_ids:
        return {}
    return {
        row.linked_block_id: row.block_id
        for row in (
            db.query(TaskBlockFeedbackLink)
            .join(TaskBlock, TaskBlock.id == TaskBlockFeedbackLink.block_id)
            .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
            .filter(
                TaskBlockFeedbackLink.linked_block_id.in_(list(block_ids)),
                TrackerTask.deleted_at.is_(None),
            )
        )
    }


def block_choice_label(block: TaskBlock, task: TrackerTask, frame: str | None) -> str:
    """Подпись блока в выборе и на метке: «Задание · Домашнее задание · Цикл 2»."""
    parts = [task.title or "Задание", block.title or BLOCK_TYPE_LABELS.get(block.block_type, "")]
    if frame:
        parts.append(frame)
    return " · ".join(part for part in parts if part)


def linked_block_choices(db: DBSession, block_ids: list[int]) -> dict[int, dict]:
    """Метки уже связанных блоков для формы правки: id и подпись."""
    if not block_ids:
        return {}
    from app.services.tracker import cycle_label

    rows = (
        db.query(TaskBlock, TrackerTask)
        .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
        .filter(TaskBlock.id.in_(block_ids))
        .all()
    )
    topics = {
        topic.id: topic
        for topic in db.query(LearningTopic).filter(
            LearningTopic.id.in_({task.topic_id for _, task in rows if task.topic_id})
        )
    } if rows else {}
    result = {}
    for block, task in rows:
        topic = topics.get(task.topic_id)
        frame = cycle_label(db, topic) if topic is not None else None
        result[block.id] = {"id": block.id, "label": block_choice_label(block, task, frame)}
    return result


def sync_links(db: DBSession, block: TaskBlock, raw_ids) -> None:
    """Связанные блоки главного блока из формы конструктора.

    Снос и пересборка, как у остальных настроек блока (`_sync_dialog_settings`).
    Блок без сдачи связей не держит: его могли переключить с домашки на текст.
    Отказ (`LinkError`) — на всё, что дало бы двусмысленную ОС: связь с самим
    собой, с блоком без сдачи или удалённым, цепочка (главный связан с
    другим главным или сам чей-то связанный) и блок, уже связанный с другим
    главным, — иначе одна работа попала бы в два общих ответа.
    """
    db.query(TaskBlockFeedbackLink).filter(
        TaskBlockFeedbackLink.block_id == block.id
    ).delete(synchronize_session=False)
    if block.block_type not in DIALOG_BLOCK_TYPES:
        return
    ids: list[int] = []
    for raw in raw_ids or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in ids:
            ids.append(value)
    if not ids:
        return
    if len(ids) > MAX_LINKED_BLOCKS:
        raise LinkError(f"С одним заданием можно связать не больше {MAX_LINKED_BLOCKS} прошлых.")
    if block.id in ids:
        raise LinkError("Задание нельзя связать с самим собой.")
    targets = {
        row.id: (row, task)
        for row, task in (
            db.query(TaskBlock, TrackerTask)
            .join(TrackerTask, TrackerTask.id == TaskBlock.task_id)
            .filter(TaskBlock.id.in_(ids))
        )
    }
    for block_id in ids:
        target = targets.get(block_id)
        if target is None or target[1].deleted_at is not None:
            raise LinkError("Связанное задание удалено – обновите страницу и выберите заново.")
        if target[0].block_type not in DIALOG_BLOCK_TYPES:
            raise LinkError("Связать можно только задания со сдачей работы.")
    if main_block_ids(db, [block.id]):
        raise LinkError(
            "Это задание уже входит в общую обратную связь другого задания – "
            "сначала уберите его оттуда."
        )
    if linked_block_ids(db, ids):
        raise LinkError(
            "Выбранное задание само собирает общую обратную связь – "
            "связывать цепочкой нельзя."
        )
    taken = {
        linked: main for linked, main in main_block_ids(db, ids).items() if main != block.id
    }
    if taken:
        raise LinkError(
            "Одно из выбранных заданий уже входит в общую обратную связь "
            "другого задания."
        )
    # Остались только связи с удалёнными главными (живые отказали выше,
    # свои снесены в начале) — уникальный `linked_block_id` иначе не даст
    # перевязать блок.
    db.query(TaskBlockFeedbackLink).filter(
        TaskBlockFeedbackLink.linked_block_id.in_(ids)
    ).delete(synchronize_session=False)
    for block_id in ids:
        db.add(TaskBlockFeedbackLink(block_id=block.id, linked_block_id=block_id))


# --- сдачи ------------------------------------------------------------------

def _own_feedback_ids(db: DBSession, submission_ids: list[int]) -> set[int]:
    if not submission_ids:
        return set()
    return {
        row.submission_id
        for row in db.query(TaskBlockFeedback.submission_id).filter(
            TaskBlockFeedback.submission_id.in_(submission_ids)
        )
    }


def absorbed_main_block(db: DBSession, submission: TaskBlockSubmission) -> int | None:
    """Главный блок, в общую ОС которого входит эта сдача, или None.

    None — блок ни с кем не связан или у сдачи уже свой диалог (куратор
    ответил на неё до связки)."""
    main = main_block_ids(db, [submission.block_id]).get(submission.block_id)
    if main is None or _own_feedback_ids(db, [submission.id]):
        return None
    return main


def main_submission_for(db: DBSession, submission: TaskBlockSubmission) -> TaskBlockSubmission | None:
    """Сданная главная работа того же ученика, если эта сдача поглощена."""
    main_block = absorbed_main_block(db, submission)
    if main_block is None:
        return None
    return (
        db.query(TaskBlockSubmission)
        .filter(
            TaskBlockSubmission.block_id == main_block,
            TaskBlockSubmission.user_id == submission.user_id,
            TaskBlockSubmission.submitted_at.isnot(None),
        )
        .first()
    )


def absorbed_submission_ids(db: DBSession, submissions: list[TaskBlockSubmission]) -> set[int]:
    """Какие из сдач поглощены общей ОС — пакетом, для очереди куратора."""
    linked = main_block_ids(db, {s.block_id for s in submissions})
    candidates = [s.id for s in submissions if s.block_id in linked]
    own = _own_feedback_ids(db, candidates)
    return {sid for sid in candidates if sid not in own}


@dataclass
class GroupWork:
    """Одна работа в общей ОС: что показать куратору и ученику."""
    submission: TaskBlockSubmission
    task_title: str
    block_title: str
    frame: str | None
    submitted_label: str
    images: list[str] = field(default_factory=list)
    is_main: bool = False


@dataclass
class WorkGroup:
    """Общая ОС: главная работа и поглощённые, по дате сдачи."""
    works: list[GroupWork]

    @property
    def period_label(self) -> str:
        """«Работы с 04.10.2026 по 13.10.2026» — шапка в очереди и на экране."""
        dates = [w.submission.submitted_at for w in self.works if w.submission.submitted_at]
        if not dates:
            return ""
        first, last = msk_date_text(min(dates)), msk_date_text(max(dates))
        return f"Работы с {first} по {last}" if first != last else f"Работы за {first}"

    @property
    def linked(self) -> list[GroupWork]:
        return [w for w in self.works if not w.is_main]


def absorbed_for_mains(
    db: DBSession, mains: list[TaskBlockSubmission]
) -> dict[int, list[TaskBlockSubmission]]:
    """Главная сдача → поглощённые сдачи того же ученика по связанным блокам.
    Пакетом на много главных — для очереди и списков."""
    links = linked_block_ids(db, list({m.block_id for m in mains}))
    linked_ids = {bid for ids in links.values() for bid in ids}
    if not linked_ids:
        return {}
    rows = (
        db.query(TaskBlockSubmission)
        .filter(
            TaskBlockSubmission.block_id.in_(linked_ids),
            TaskBlockSubmission.user_id.in_({m.user_id for m in mains}),
            TaskBlockSubmission.submitted_at.isnot(None),
        )
        .all()
    )
    own = _own_feedback_ids(db, [r.id for r in rows])
    by_key: dict[tuple[int, int], TaskBlockSubmission] = {
        (r.block_id, r.user_id): r for r in rows if r.id not in own
    }
    result: dict[int, list[TaskBlockSubmission]] = {}
    for main in mains:
        found = [
            by_key[(bid, main.user_id)]
            for bid in links.get(main.block_id, [])
            if (bid, main.user_id) in by_key
        ]
        if found:
            result[main.id] = found
    return result


def work_group(db: DBSession, main: TaskBlockSubmission) -> WorkGroup | None:
    """Общая ОС главной сдачи; None — у блока нет связанных работ ученика."""
    absorbed = absorbed_for_mains(db, [main]).get(main.id)
    if not absorbed:
        return None
    from app.services.tracker import cycle_label

    submissions = [main] + absorbed
    blocks = {
        b.id: b for b in db.query(TaskBlock).filter(
            TaskBlock.id.in_({s.block_id for s in submissions})
        )
    }
    tasks = {
        t.id: t for t in db.query(TrackerTask).filter(
            TrackerTask.id.in_({b.task_id for b in blocks.values()})
        )
    }
    topics = {
        topic.id: topic for topic in db.query(LearningTopic).filter(
            LearningTopic.id.in_({t.topic_id for t in tasks.values() if t.topic_id})
        )
    }
    images: dict[int, list[str]] = {}
    for image in (
        db.query(TaskBlockSubmissionImage)
        .filter(TaskBlockSubmissionImage.submission_id.in_([s.id for s in submissions]))
        .order_by(TaskBlockSubmissionImage.sort_order, TaskBlockSubmissionImage.id)
    ):
        images.setdefault(image.submission_id, []).append(image.image_s3_url)
    works = []
    for submission in submissions:
        block = blocks[submission.block_id]
        task = tasks[block.task_id]
        topic = topics.get(task.topic_id)
        works.append(GroupWork(
            submission=submission,
            task_title=task.title or "Задание",
            block_title=block.title or BLOCK_TYPE_LABELS.get(block.block_type, ""),
            frame=cycle_label(db, topic) if topic is not None else None,
            submitted_label=msk_date_text(submission.submitted_at),
            images=images.get(submission.id, []),
            is_main=submission.id == main.id,
        ))
    # Все сдачи здесь сданы (`submitted_at` есть у главной и у выборки выше).
    works.sort(key=lambda w: w.submission.submitted_at)
    return WorkGroup(works=works)
