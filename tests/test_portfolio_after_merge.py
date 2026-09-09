"""Раздел «После» = работы портфолио + работы, сданные внутри заданий.

Владелец 09.09.2026: «все остальные работы в ПОСЛЕ попадают, когда
пользователь сдает в заданиях». Слияние только на показ — в базе сдачи
остаются в `task_block_submissions`, копий в `works` не появляется.
"""
from datetime import datetime, timedelta, timezone

from app.models.task_block import (
    BLOCK_UPLOAD,
    TaskBlock,
    TaskBlockSubmission,
    TaskBlockSubmissionImage,
)
from app.models.work import WORK_TYPE_AFTER, WORK_TYPE_BEFORE, Work
from app.services.portfolio import SOURCE_SUBMISSION, SOURCE_WORK, after_gallery_groups
from app.services.program import day_bounds
from app.services.tracker import create_task
from app.services.tz import today_msk

TODAY = today_msk()


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _work(db, user, *, month="январь", year=2026, work_type=WORK_TYPE_AFTER,
          status="success", filename="after.jpg"):
    work = Work(
        user_id=user.id, work_type=work_type, month=month, year=year,
        filename=filename, s3_url=f"https://s3.example/{filename}", status=status,
    )
    db.add(work)
    db.commit()
    return work


def _submission(db, user, *, submitted_at, images=1):
    task = create_task(
        db, title="День с работой", user_id=user.id, kind="material",
        due_at=day_bounds(TODAY)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_UPLOAD, title="Загрузите работы",
        sort_order=1, is_required=True,
    )
    db.add(block)
    db.flush()
    submission = TaskBlockSubmission(
        block_id=block.id, user_id=user.id,
        submitted_at=_utc(submitted_at) if submitted_at else None,
    )
    db.add(submission)
    db.flush()
    for n in range(images):
        db.add(TaskBlockSubmissionImage(
            submission_id=submission.id,
            image_s3_url=f"https://s3.example/submission-{submission.id}-{n}.jpg",
            sort_order=n,
        ))
    db.commit()
    return submission


def _january(year=2026, day=15, hour=12):
    return datetime(year, 1, day, hour, tzinfo=timezone.utc)


# ── сборка галереи ──────────────────────────────────────────────────────────

def test_submitted_work_appears_in_the_month_of_its_submission(db, regular_user):
    _submission(db, regular_user, submitted_at=_january())

    groups = after_gallery_groups(db, regular_user.id)

    assert [(g["year"], g["month"]) for g in groups] == [(2026, "январь")]
    assert groups[0]["total"] == 1
    assert groups[0]["works"][0].source == SOURCE_SUBMISSION


def test_work_and_submission_of_one_month_share_a_group(db, regular_user):
    _work(db, regular_user, month="январь", year=2026)
    _submission(db, regular_user, submitted_at=_january(), images=2)

    groups = after_gallery_groups(db, regular_user.id)

    assert len(groups) == 1
    assert groups[0]["total"] == 3
    # Массовое удаление месяца считается только по настоящим работам: сдачи в
    # заданиях bulk-роут работ не трогает.
    assert groups[0]["work_total"] == 1


def test_unsubmitted_draft_is_not_shown(db, regular_user):
    """Строка сдачи заводится первой загрузкой файла; пока `submitted_at`
    пуст, работа не сдана и в портфолио не показывается."""
    _submission(db, regular_user, submitted_at=None)

    assert after_gallery_groups(db, regular_user.id) == []


def test_before_work_never_leaks_into_after(db, regular_user):
    _work(db, regular_user, work_type=WORK_TYPE_BEFORE, filename="before.jpg")

    assert after_gallery_groups(db, regular_user.id) == []


def test_failed_work_is_skipped(db, regular_user):
    _work(db, regular_user, status="failed")

    assert after_gallery_groups(db, regular_user.id) == []


def test_nothing_is_copied_into_works_table(db, regular_user):
    """Слияние — только на показ: сдача остаётся в своей таблице."""
    _submission(db, regular_user, submitted_at=_january())

    after_gallery_groups(db, regular_user.id)

    assert db.query(Work).count() == 0


# ── экраны ──────────────────────────────────────────────────────────────────

def test_student_portfolio_page_shows_submitted_work(auth_client, db):
    client, user = auth_client
    submission = _submission(db, user, submitted_at=_january())

    resp = client.get("/cabinet/portfolio")

    assert resp.status_code == 200
    assert f"submission-{submission.id}-0.jpg" in resp.text


def test_staff_json_marks_the_source_of_every_photo(client, db, user_factory, session_factory):
    student = user_factory(vk_id=700_001, role_name="ученик")
    admin = user_factory(vk_id=700_002, name="Admin Test", role_name="админ")
    _work(db, student, month="январь", year=2026)
    _submission(db, student, submitted_at=_january())
    client.cookies.set("session_id", session_factory(admin).id)

    payload = client.get(f"/cabinet/students/{student.id}/portfolio").json()

    group = payload["after_by_month"][0]
    assert group["total"] == 2
    assert group["work_total"] == 1
    assert sorted(w["source"] for w in group["works"]) == [SOURCE_SUBMISSION, SOURCE_WORK]


def test_staff_json_returns_before_as_a_flat_list(client, db, user_factory, session_factory):
    """«До» без месяцев (владелец 09.09.2026) — плоский список вместо групп."""
    student = user_factory(vk_id=700_003, role_name="ученик")
    admin = user_factory(vk_id=700_004, name="Admin Flat", role_name="админ")
    _work(db, student, work_type=WORK_TYPE_BEFORE, month="январь", filename="b1.jpg")
    _work(db, student, work_type=WORK_TYPE_BEFORE, month="февраль", filename="b2.jpg")
    client.cookies.set("session_id", session_factory(admin).id)

    payload = client.get(f"/cabinet/students/{student.id}/portfolio").json()

    assert "before_by_month" not in payload
    assert len(payload["before_flat"]) == 2
    assert {w["source"] for w in payload["before_flat"]} == {SOURCE_WORK}
