"""Generic free-text tags for users (admin/superadmin tagging tool)."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session as DBSession

from app.constants import MOCK_SUBJECTS
from app.models.role import Role
from app.models.tag import Tag, UserTag
from app.models.user import User
from app.models.work import Work, WORK_TYPE_MOCK_EXAM
from app.services.utils import has_case_growth


def get_or_create_tag(db: DBSession, name: str) -> Tag:
    """Case-insensitive get-or-create.

    Matching is done in Python (not SQL `lower()`) because SQLite's built-in
    LOWER() only folds ASCII and would treat "Рисунок"/"рисунок" as distinct.
    The tags table is small, so fetching all rows is cheap.
    """
    name_clean = name.strip()[:50]
    name_lower = name_clean.lower()
    for tag in db.query(Tag).all():
        if tag.name.lower() == name_lower:
            return tag
    tag = Tag(name=name_clean)
    db.add(tag)
    db.flush()
    return tag


def add_tag_to_user(db: DBSession, user_id: int, tag_id: int) -> bool:
    """Returns True if a new link was created, False if it already existed."""
    existing = db.get(UserTag, (user_id, tag_id))
    if existing:
        return False
    db.add(UserTag(user_id=user_id, tag_id=tag_id))
    db.flush()
    return True


def remove_tag_from_user(db: DBSession, user_id: int, tag_id: int) -> bool:
    existing = db.get(UserTag, (user_id, tag_id))
    if not existing:
        return False
    db.delete(existing)
    db.flush()
    return True


def get_tags_for_users(db: DBSession, user_ids: list[int]) -> dict[int, list[Tag]]:
    if not user_ids:
        return {}
    rows = (
        db.query(UserTag.user_id, Tag)
        .join(Tag, Tag.id == UserTag.tag_id)
        .filter(UserTag.user_id.in_(user_ids))
        .order_by(Tag.name)
        .all()
    )
    result: dict[int, list[Tag]] = defaultdict(list)
    for user_id, tag in rows:
        result[user_id].append(tag)
    return dict(result)


def _students_with_case_growth(db: DBSession, user_ids: list[int]) -> set[int]:
    rows = (
        db.query(Work.user_id, Work.subject, Work.score, Work.month, Work.year,
                 Work.scored_at, Work.created_at, Work.work_type)
        .filter(
            Work.user_id.in_(user_ids),
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.status == "success",
            Work.score.isnot(None),
            Work.subject.isnot(None),
        )
        .all()
    )
    grouped: dict[int, list] = defaultdict(list)
    for row in rows:
        grouped[row.user_id].append(row)
    return {uid for uid, works in grouped.items() if has_case_growth(works)}


def ensure_profile_tags(db: DBSession, students: list[User]) -> None:
    """Auto-create tags from each student's registration choices (course
    period, lesson count, tariff) and mock-exam case growth, so admins can
    use the regular tag chips to segment students by them.

    Idempotent — once created, these tags behave like any other tag and are
    independent from the underlying User columns (removing one here does not
    change course_periods/tariff/lessons_count).
    """
    if not students:
        return

    case_ids = _students_with_case_growth(db, [s.id for s in students])

    for student in students:
        names: set[str] = set()
        if student.course_periods:
            for period in student.course_periods.split(","):
                period = period.strip()
                if period:
                    names.add(period.split(" ")[0])
        if student.lessons_count:
            names.add(student.lessons_count.strip())
        if student.tariff:
            names.add(student.tariff.strip())
        if student.id in case_ids:
            names.add("КЕЙС")

        for name in names:
            tag = get_or_create_tag(db, name)
            add_tag_to_user(db, student.id, tag.id)

    db.commit()


def parse_usernames(raw: str) -> list[str]:
    """Parse a pasted list of @usernames (newline/comma/semicolon separated).

    Strips '@', lowercases, dedupes while preserving order.
    """
    seen: set[str] = set()
    usernames: list[str] = []
    for item in raw.replace(",", "\n").replace(";", "\n").splitlines():
        username = item.strip().lstrip("@").lower()
        if username and username not in seen:
            usernames.append(username)
            seen.add(username)
    return usernames


def get_curator_names(db: DBSession) -> list[str]:
    """Full names of active curators (role rank 2), used both as suggested
    tags and to colour curator-name chips distinctly."""
    curators = (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(Role.rank == 2, User.is_active == True, User.deleted_at.is_(None))  # noqa: E712
        .all()
    )
    names: set[str] = set()
    for c in curators:
        full_name = f"{c.last_name or ''} {c.first_name or c.name}".strip()
        if full_name:
            names.add(full_name)
    return sorted(names)


def get_all_tags(db: DBSession) -> list[Tag]:
    """All tags that exist, sorted by name — used to populate filter dropdowns."""
    return db.query(Tag).order_by(Tag.name).all()


def get_suggested_tags(db: DBSession) -> list[str]:
    names: set[str] = set(MOCK_SUBJECTS)
    names.update(get_curator_names(db))

    for (tag_name,) in db.query(Tag.name).all():
        names.add(tag_name)

    return sorted(names)
