"""Статистика демо по назначенным сдачам, независимо от очереди проверки."""
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.constants import MOCK_SUBJECTS, PROGRAM_ANNUAL
from app.models.learning_topic import LearningTopic, TOPIC_KIND_STAGE, TOPIC_KIND_WEEK
from app.models.task_block import (
    BLOCK_TIMED, SUBMISSION_BLOCK_TYPES, TaskBlock, TaskBlockAnswer,
    TaskBlockAnswerOption, TaskBlockResponse, TaskBlockState, TaskBlockSubmission,
    TaskBlockSubmissionImage,
)
from app.models.tracker import ITEM_MOCK_EXAM, TrackerTask
from app.models.user import User
from app.services.program import msk_date
from app.services.report_scope import reportable_students_q
from app.services.staff_dashboard import CURATOR_STATS_TARIFFS
from app.services.task_blocks import (
    BlockViewer, feed_visible_blocks, get_audiences, get_required_tariffs,
    is_submission_complete, required_by_block_for_task,
)
from app.services.tracker import cycle_bounds
from app.services.tz import MSK_TZ
from app.services.video_topics import (
    _published_topic_clause, program_topic_filter, topic_audience_user_ids,
)

UNASSIGNED_SUBJECT = "Без предмета"


def _student_row(student: User) -> dict:
    return {
        "id": student.id,
        "name": " ".join(filter(None, (student.last_name, student.first_name))) or student.name,
        "tariff": student.tariff,
        "last_seen": msk_date(student.last_seen_at) if student.last_seen_at else None,
    }


def _period(db: Session, today: date, stage_id: int | None, cycle_id: int | None) -> dict:
    topics = db.query(LearningTopic).filter(
        LearningTopic.kind.in_((TOPIC_KIND_WEEK, TOPIC_KIND_STAGE)),
        _published_topic_clause(), program_topic_filter(PROGRAM_ANNUAL),
    ).order_by(LearningTopic.opens_at.desc(), LearningTopic.id.desc()).all()
    stages = {topic.id: topic.title for topic in topics if topic.kind == TOPIC_KIND_STAGE}
    cycles = [topic for topic in topics if topic.kind == TOPIC_KIND_WEEK]
    stage_ids = {cycle.parent_id for cycle in cycles}
    stages = {key: label for key, label in stages.items() if key in stage_ids}
    if any(cycle.parent_id not in stages for cycle in cycles):
        stages[0] = "Без месяца"

    def stage_of(cycle):
        return cycle.parent_id if cycle.parent_id in stages else 0

    if stage_id not in stages:
        stage_id = None
    options = [cycle for cycle in cycles if stage_id is None or stage_of(cycle) == stage_id]
    selected = next((cycle for cycle in options if cycle.id == cycle_id), None)
    if selected is None:
        selected = next((cycle for cycle in options if cycle_bounds(cycle)[0] <= today <= cycle_bounds(cycle)[1]), None)
    if selected is None and stage_id is not None and options and cycle_id:
        selected = options[0]
    if stage_id is None and selected is not None and cycle_id is None:
        stage_id = stage_of(selected)
        options = [cycle for cycle in cycles if stage_of(cycle) == stage_id]
    return {"stages": stages, "stage_id": stage_id, "cycles": options, "cycle": selected}


def _activity(db: Session, pairs: list, student_ids: set[int]) -> tuple[set, set, dict]:
    blocks = {block.id: block for block, _ in pairs}
    if not blocks or not student_ids:
        return set(), set(), {}
    complete, started = set(), set()
    submissions = db.query(TaskBlockSubmission, func.count(TaskBlockSubmissionImage.id)).outerjoin(
        TaskBlockSubmissionImage, TaskBlockSubmissionImage.submission_id == TaskBlockSubmission.id,
    ).filter(
        TaskBlockSubmission.block_id.in_(blocks), TaskBlockSubmission.user_id.in_(student_ids),
    ).group_by(TaskBlockSubmission.id).all()
    for submission, count in submissions:
        key = (submission.user_id, submission.block_id)
        if count:
            started.add(key)
        if submission.submitted_at and is_submission_complete(blocks[submission.block_id], count):
            complete.add(key)

    timed_ids = [block.id for block in blocks.values() if block.block_type == BLOCK_TIMED]
    if timed_ids:
        started.update(db.query(TaskBlockState.user_id, TaskBlockState.block_id).filter(
            TaskBlockState.block_id.in_(timed_ids), TaskBlockState.user_id.in_(student_ids),
            TaskBlockState.started_at.is_not(None),
        ).all())

    task_ids = {task.id for _, task in pairs}
    # Ответ относится к заданию. Явный предмет блока ответа сужает его до этого предмета.
    answers = defaultdict(set)
    for uid, task_id, subject in db.query(
        TaskBlockResponse.user_id, TaskBlockResponse.task_id, TaskBlock.subject,
    ).join(TaskBlockAnswer, TaskBlockAnswer.response_id == TaskBlockResponse.id).join(
        TaskBlock, TaskBlock.id == TaskBlockAnswer.block_id,
    ).outerjoin(TaskBlockAnswerOption, TaskBlockAnswerOption.answer_id == TaskBlockAnswer.id).filter(
        TaskBlockResponse.user_id.in_(student_ids), TaskBlockResponse.task_id.in_(task_ids),
        or_(func.length(func.trim(TaskBlockAnswer.text)) > 0, TaskBlockAnswerOption.answer_id.is_not(None)),
    ).distinct():
        answers[(uid, task_id)].add(subject)
    return complete, started, answers


def _assignments(db: Session, cycle: LearningTopic, students: list[User], now: datetime) -> dict:
    """Учитываем назначенные блоки, включая ожидающие предыдущей сдачи."""
    if not students:
        return {}
    pairs = db.query(TaskBlock, TrackerTask).join(TrackerTask, TrackerTask.id == TaskBlock.task_id).filter(
        TrackerTask.topic_id == cycle.id,
        TrackerTask.is_published.is_(True), TrackerTask.deleted_at.is_(None),
        TrackerTask.kind != ITEM_MOCK_EXAM,
        or_(TrackerTask.starts_at.is_(None), TrackerTask.starts_at <= now),
        or_(TaskBlock.opens_at.is_(None), TaskBlock.opens_at <= now),
        TaskBlock.block_type.in_(SUBMISSION_BLOCK_TYPES),
    ).order_by(TrackerTask.sort_order, TrackerTask.id, TaskBlock.sort_order, TaskBlock.id).all()
    if not pairs:
        return {}
    audience = topic_audience_user_ids(db, cycle.id)
    students = [student for student in students if student.id in audience and student.is_group_member]
    complete, started, answers = _activity(db, pairs, {student.id for student in students})
    block_ids = [block.id for block, _ in pairs]
    audiences = get_audiences(db, block_ids)
    required_tariffs = get_required_tariffs(db, block_ids)
    blocks_by_task = defaultdict(list)
    tasks = {}
    for block, task in pairs:
        blocks_by_task[task.id].append(block)
        tasks[task.id] = task
    required = {
        block_id: flag
        for task_id, blocks in blocks_by_task.items()
        for block_id, flag in required_by_block_for_task(tasks[task_id], blocks, is_intake_student=False).items()
    }
    result = defaultdict(list)
    for student in students:
        viewer = BlockViewer(db, user_id=student.id, tariff=student.tariff, study_subject=student.study_subject)
        for block, task in pairs:
            if not feed_visible_blocks([block], audiences, viewer):
                continue
            key = (student.id, block.id)
            subject = block.subject or task.subject
            answer_subjects = answers.get((student.id, task.id), ())
            label = f"{task.title} · {block.title}" if block.title and block.title != task.title else task.title
            result[student.id].append({
                "label": label, "subject": subject,
                "required": required[block.id] and (
                    not required_tariffs.get(block.id) or student.tariff in required_tariffs[block.id]
                ),
                "done": key in complete,
                "started": (
                    key in started or (subject is not None and subject in answer_subjects)
                    or (subject == task.subject and None in answer_subjects)
                ),
            })
    return result


def _progress(student: dict, blocks: list[dict]) -> dict:
    remaining = [block["label"] for block in blocks if not block["done"]]
    return {**student, "total": len(blocks), "done": len(blocks) - len(remaining), "remaining": remaining}


def build_curator_demo(
    db: Session, curator_id: int, *, stage_id: int | None = None, cycle_id: int | None = None,
    subject: str = "", now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(MSK_TZ).date()
    period = _period(db, today, stage_id, cycle_id)
    students = reportable_students_q(db, now).filter(
        User.curator_id == curator_id, User.tariff.in_(CURATOR_STATS_TARIFFS),
    ).all()
    student_rows = sorted((_student_row(student) for student in students), key=lambda row: (row["name"].casefold(), row["id"]))
    cycle = period["cycle"]
    future = bool(cycle is not None and cycle_bounds(cycle)[0] > today)
    assigned = _assignments(db, cycle, students, now) if cycle is not None and not future else {}
    subjects = list(MOCK_SUBJECTS)
    if any(not block["subject"] for blocks in assigned.values() for block in blocks):
        subjects.append(UNASSIGNED_SUBJECT)
    subject = subject if subject in subjects else subjects[0]
    weekly = {tariff: {"done": [], "remaining": []} for tariff in CURATOR_STATS_TARIFFS}
    practice = {"done": [], "started": [], "none": []}
    for row in student_rows:
        blocks = assigned.get(row["id"], [])
        required = [block for block in blocks if block["required"]]
        if required:
            result = _progress(row, required)
            weekly[row["tariff"]]["remaining" if result["remaining"] else "done"].append(result)
        matching = [block for block in blocks if (block["subject"] or UNASSIGNED_SUBJECT) == subject]
        if matching:
            result = _progress(row, matching)
            if not result["remaining"]:
                group = "done"
            elif result["done"] or any(block["started"] for block in matching):
                group = "started"
            else:
                group = "none"
            practice[group].append(result)

    # Три завершившихся календарных дня между последним визитом и сегодня.
    absent = [row for row in student_rows if row["last_seen"] and (today - row["last_seen"]).days > 3]
    return {
        **period, "future": future, "subjects": subjects, "subject": subject,
        "weekly": weekly, "practice": practice, "practice_total": sum(map(len, practice.values())),
        "absent": absent, "unknown": [row for row in student_rows if row["last_seen"] is None],
    }
