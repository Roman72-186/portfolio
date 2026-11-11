"""Отдельный узкий доступ к посещаемости очников первой недели.

Тег задаёт группу, а посещение хранится у конкретной ссылки занятия.
Тарифный просмотр сотрудника не расширяется на остальные карточки школы.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.learning_topic import LearningTopic
from app.models.role import Role
from app.models.tag import Tag, UserTag
from app.models.task_block import BLOCK_LINK, TaskBlock, LessonAttendanceMark
from app.models.tracker import TrackerTask
from app.models.user import User

ATTENDANCE_ACTION = "students:first_week_attendance"
COHORT_TAG = "Очник"
PAGE_URL = "/cabinet/staff/first-week-attendance"


def cohort_students(db: Session, user_ids=None) -> list[User]:
    tag_ids = [tag.id for tag in db.query(Tag) if tag.name.casefold() == COHORT_TAG.casefold()]
    if not tag_ids:
        return []
    query = db.query(User).join(Role, Role.id == User.role_id).join(UserTag, UserTag.user_id == User.id).filter(
        UserTag.tag_id.in_(tag_ids), Role.rank == 1, User.is_active.is_(True),
        User.deleted_at.is_(None), User.archived_at.is_(None), User.program == "annual",
    )
    if user_ids is not None:
        query = query.filter(User.id.in_(user_ids))
    return query.order_by(User.id).all()


def first_week_ids(db: Session) -> set[int]:
    """Первый цикл каждого годового этапа по открытию, без угадывания названий.

    Неопубликованная первая неделя не делает следующую «первой».
    Легаси-циклы без этапа и каникулярные наборы в исключение не входят.
    """
    stages = {t.id for t in db.query(LearningTopic).filter(
        LearningTopic.kind == "stage", LearningTopic.program == "annual",
        LearningTopic.deleted_at.is_(None), LearningTopic.is_published.is_(True),
    )}
    first = {}
    for topic in db.query(LearningTopic).filter(
        LearningTopic.kind == "week", LearningTopic.program == "annual",
        LearningTopic.parent_id.in_(stages), LearningTopic.deleted_at.is_(None),
    ).order_by(LearningTopic.opens_at, LearningTopic.id):
        first.setdefault(topic.parent_id, topic)
    return {topic.id for topic in first.values() if topic.is_published}


def exemptions_by_student(db: Session, blocks_by_task: dict[int, list[TaskBlock]], user_ids) -> dict[int, set[int]]:
    """Пакетный расчёт: лента, завершение задания и статистика используют его вместе."""
    from app.services.meeting_joins import attended_link_ids
    from app.services.task_blocks import attendance_links

    students = cohort_students(db, user_ids)
    if not students or not blocks_by_task:
        return {}
    first = first_week_ids(db)
    tasks = db.query(TrackerTask).filter(TrackerTask.id.in_(blocks_by_task), TrackerTask.topic_id.in_(first)).all()
    links = {}
    for task in tasks:
        # Здесь снимается только обязанность смотреть связанную запись.
        # Теоретические видео перед ссылкой и другие недели не затрагиваются.
        links.update(attendance_links(blocks_by_task[task.id], include_all_videos=True))
    ids = {link for link in links.values() if link is not None}
    result = {}
    for student in students:
        attended = attended_link_ids(db, student.id, ids)
        result[student.id] = {video for video, link in links.items() if link in attended}
    return result


def recording_exemptions(db: Session, user_id: int, blocks: list[TaskBlock]) -> set[int]:
    if not blocks:
        return set()
    return exemptions_by_student(db, {blocks[0].task_id: blocks}, [user_id]).get(user_id, set())


def lesson_rows(db: Session):
    now = datetime.now(timezone.utc)
    rows = db.query(TaskBlock, TrackerTask, LearningTopic).join(
        TrackerTask, TrackerTask.id == TaskBlock.task_id,
    ).join(LearningTopic, LearningTopic.id == TrackerTask.topic_id).filter(
        LearningTopic.id.in_(first_week_ids(db)), LearningTopic.opens_at <= now,
        TrackerTask.is_published.is_(True), TrackerTask.deleted_at.is_(None),
        TaskBlock.block_type == BLOCK_LINK, TaskBlock.url.isnot(None),
    ).order_by(LearningTopic.opens_at.desc(), TrackerTask.id.desc(), TaskBlock.sort_order).all()
    return [row for row in rows if not (
        row[1].starts_at and _utc(row[1].starts_at) > now
        or row[0].opens_at and _utc(row[0].opens_at) > now
    )]


def _utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def eligible_students(db: Session, block: TaskBlock) -> list[User]:
    from app.services.task_blocks import BlockViewer, get_audiences, feed_visible_blocks
    from app.services.tracker import task_audience_user_ids

    students = cohort_students(db)
    audience = task_audience_user_ids(db, block.task_id)
    rules = get_audiences(db, [block.id])
    return [student for student in students if student.id in audience and feed_visible_blocks(
        [block], rules, BlockViewer(db, user_id=student.id, tariff=student.tariff, study_subject=student.study_subject),
    )]


def page_data(db: Session, lesson_id: int | None = None):
    rows = lesson_rows(db)
    chosen = next((row for row in rows if row[0].id == lesson_id), None) if lesson_id else next(iter(rows), None)
    if lesson_id and chosen is None:
        raise ValueError("Занятие первой недели не найдено")
    lessons = [{"id": block.id, "label": f"{cycle.title} · {task.title} · {block.subject or task.subject or 'Общее занятие'} · {block.title or 'Подключиться'}"}
               for block, task, cycle in rows]
    students = []
    if chosen:
        from app.services.meeting_joins import attendance_by_block, ABSENT_STAFF, ATTENDED_PRESSED
        block = chosen[0]
        attendance = attendance_by_block(db, [block.id])
        marks = {m.user_id: m for m in db.query(LessonAttendanceMark).filter_by(block_id=block.id)}
        for student in eligible_students(db, block):
            mark = marks.get(student.id)
            source = attendance.get((block.id, student.id))
            label = "Нет отметки"
            if source == ABSENT_STAFF:
                label = "Не был"
            elif mark and mark.attended:
                label = {"in_person": "Был очно", "online": "Был онлайн"}.get(mark.attendance_format, "Был (отметил преподаватель)")
            elif source == ATTENDED_PRESSED:
                label = "Подключился онлайн через кнопку"
            students.append({"id": student.id, "name": student.name or "Ученик", "status": label})
    return {"lessons": lessons, "lesson_id": chosen[0].id if chosen else None, "students": students}


def mark_attendance(db: Session, *, student_id: int, block_id: int, attendance: str, staff_id: int):
    from app.services.meeting_joins import set_attendance

    row = next((row for row in lesson_rows(db) if row[0].id == block_id), None)
    if row is None or student_id not in {student.id for student in eligible_students(db, row[0])}:
        raise ValueError("У ученика нет такого занятия первой недели")
    set_attendance(db, block=row[0], student_id=student_id, attended=attendance != "absent",
                   staff_id=staff_id, attendance_format=attendance if attendance != "absent" else None)


def resolve_configuration(db: Session, handles: list[str], moderator_name: str, expected_count: int):
    """Точный поиск перед массовой настройкой; никаких совпадений по части ника."""
    normalized = [handle.strip().lstrip("@").casefold() for handle in handles]
    if len(normalized) != expected_count or len(set(normalized)) != expected_count or not all(normalized):
        raise ValueError("Количество уникальных аккаунтов не совпадает с ожидаемым")
    students = db.query(User).join(Role, Role.id == User.role_id).filter(
        Role.rank == 1, User.deleted_at.is_(None),
    ).all()
    selected = []
    for index, handle in enumerate(normalized, 1):
        matches = [student for student in students if (student.tg_username or "").lstrip("@").casefold() == handle]
        if len(matches) != 1 or not matches[0].is_active or matches[0].archived_at is not None or matches[0].program != "annual":
            raise ValueError(f"Неоднозначный или недоступный аккаунт в строке {index}")
        selected.append(matches[0])
    staff = db.query(User, Role).join(Role, Role.id == User.role_id).filter(
        Role.rank.in_([3, 4]), User.is_active.is_(True), User.deleted_at.is_(None), User.archived_at.is_(None),
    ).all()
    wanted = moderator_name.casefold().replace("ё", "е")
    moderators = [user for user, role in staff if role.rank == 3 and wanted in
                  ((user.name or "") + " " + (user.first_name or "")).casefold().replace("ё", "е").split()]
    heads = [user for user, role in staff if role.rank == 4]
    if len(moderators) != 1 or len(heads) != 1:
        raise ValueError("Аккаунты модератора или главного преподавателя определены неоднозначно")
    return selected, [moderators[0], heads[0]]


def configure_cohort(db: Session, students: list[User], staff: list[User]) -> int:
    """Вызывается в транзакции только после проверки полного списка."""
    from app.models.section_access import SectionAccessRule
    from app.services.tags import get_or_create_tag, add_tag_to_user

    changed = 0
    tag = get_or_create_tag(db, COHORT_TAG)
    existing_members = {row.user_id for row in db.query(UserTag).filter_by(tag_id=tag.id)}
    if existing_members - {student.id for student in students}:
        raise ValueError("В группе очников есть аккаунты вне согласованного списка")
    tag.is_hidden = False
    for student in students:
        changed += int(add_tag_to_user(db, student.id, tag.id))
        if student.study_mode != "offline":
            student.study_mode = "offline"
            changed += 1
    for user in staff:
        rule = db.query(SectionAccessRule).filter_by(user_id=user.id, section_key=ATTENDANCE_ACTION).one_or_none()
        if rule is None:
            db.add(SectionAccessRule(user_id=user.id, section_key=ATTENDANCE_ACTION, level="edit"))
            changed += 1
        elif rule.level != "edit":
            rule.level = "edit"
            changed += 1
    db.flush()
    return changed
