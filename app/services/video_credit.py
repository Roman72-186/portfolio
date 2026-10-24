"""Ручной зачёт просмотра видео сотрудником (владелец 10.10.2026).

Повод — ученица id 288: плеер три дня не передавал странице минуту, просмотр
не засчитывался, кружок отказал 39 раз, а засчитать было нечем, кроме скрипта
на сервере. Владелец: «кнопку для СА и ГП и для конкретного ролика — список
видео, к какому циклу относится, и кнопка „Засчитать просмотр“».

Зачёт закрывает видео-блок тем же `close_block_for_user`, что и кружок
ученика: следом сами закрываются задание и всё, что от него зависит.
Источник — `video_staff`, сотрудник — в `completed_by_id` (по нему статистика
делит «закрыл ученик / система / преподаватель»). `VideoProgress` не
трогается: статистика просмотра остаётся честной — ролик засчитан, но не
просмотрен.
"""

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session as DBSession

from app.models.learning_topic import TOPIC_KIND_STAGE, TOPIC_KIND_WEEK, LearningTopic
from app.models.learning_video import LearningVideo
from app.models.task_block import BLOCK_VIDEO, TaskBlock, TaskBlockState
from app.models.tracker import ITEM_MOCK_EXAM, STATUS_DONE, TrackerTask
from app.models.user import User
from app.services.task_blocks import (
    BlockViewer,
    close_block_for_user,
    get_states,
    visible_blocks_for_student,
)
from app.services.tracker import accessible_task_ids
from app.services.video_topics import accessible_topic_ids

SOURCE_STAFF_CREDIT = "video_staff"


def _place(topic: LearningTopic | None) -> str:
    if topic is None:
        return "Без цикла"
    if topic.kind == TOPIC_KIND_WEEK:
        return f"Цикл «{topic.title}»"
    if topic.kind == TOPIC_KIND_STAGE:
        return f"Этап «{topic.title}»"
    return topic.title


def student_video_blocks(db: DBSession, student: User) -> list[dict]:
    """Видео-блоки, которые ученик видит в ленте, — с циклом и отметкой.

    Тот же доступ, что у ученика (`accessible_topic_ids`/`accessible_task_ids`,
    тариф — `visible_blocks_for_student`): засчитать можно только то, что ему
    вообще поставлено. Пробник исключён, как и в контроле просмотра
    (`cabinet_tracker._video_block_requires_completion`).
    """
    topic_ids = accessible_topic_ids(db, student.id)
    task_ids = accessible_task_ids(db, student.id)
    tasks = {
        task.id: task
        for task in db.query(TrackerTask).filter(
            TrackerTask.deleted_at.is_(None),
            TrackerTask.is_published.is_(True),
            TrackerTask.kind != ITEM_MOCK_EXAM,
            or_(
                TrackerTask.topic_id.in_(topic_ids),
                and_(TrackerTask.topic_id.is_(None), TrackerTask.id.in_(task_ids)),
            ),
        )
    }
    if not tasks:
        return []
    blocks = (
        db.query(TaskBlock)
        .filter(
            TaskBlock.task_id.in_(tasks),
            TaskBlock.block_type == BLOCK_VIDEO,
            TaskBlock.video_id.isnot(None),
        )
        .all()
    )
    viewer = BlockViewer(
        db, user_id=student.id, tariff=student.tariff, study_subject=student.study_subject
    )
    blocks = visible_blocks_for_student(db, blocks, viewer=viewer)
    if not blocks:
        return []
    states = get_states(db, block_ids=[b.id for b in blocks], user_id=student.id)
    videos = {
        v.id: v for v in db.query(LearningVideo).filter(
            LearningVideo.id.in_({b.video_id for b in blocks})
        )
    }
    topic_set = {tasks[b.task_id].topic_id for b in blocks} - {None}
    topics = {
        t.id: t for t in db.query(LearningTopic).filter(LearningTopic.id.in_(topic_set))
    } if topic_set else {}

    def order(block: TaskBlock):
        task = tasks[block.task_id]
        topic = topics.get(task.topic_id)
        return (
            topic.opens_at if topic else task.created_at,
            topic.sort_order if topic else 0,
            task.sort_order,
            task.id,
            block.sort_order,
            block.id,
        )

    rows = []
    for block in sorted(blocks, key=order):
        task = tasks[block.task_id]
        video = videos.get(block.video_id)
        state = states.get(block.id)
        rows.append({
            "block_id": block.id,
            "video": video.title if video else "Ролик удалён",
            "task": task.title,
            "place": _place(topics.get(task.topic_id)),
            "done": state is not None and state.status == STATUS_DONE,
        })
    return rows


def credit_video_block(
    db: DBSession, student: User, block_id: int, *, staff_id: int
) -> TaskBlockState | None:
    """Засчитать ученику видео-блок. `None` — блока у ученика нет.

    Уже закрытый блок не трогается: ни время, ни кто закрыл не меняются.
    """
    if not any(row["block_id"] == block_id for row in student_video_blocks(db, student)):
        return None
    block = db.get(TaskBlock, block_id)
    already_done = any(
        s.status == STATUS_DONE
        for s in get_states(db, block_ids=[block_id], user_id=student.id).values()
    )
    state = close_block_for_user(db, block, student.id, source=SOURCE_STAFF_CREDIT)
    if not already_done:
        state.completed_by_id = staff_id
    return state
