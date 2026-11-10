"""Migration and student/editor contracts for the four self-check steps."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from app.models.learning_video import LearningVideo
from app.models.task_block import TaskBlock, TaskBlockState, TaskBlockSubmission
from app.models.tracker import TrackerTask
from app.models.work import Work
from app.services.video_catalog import block_bound_video_ids, is_video_accessible
from tests.test_routes_task_blocks import _material_task_with_blocks, _staff_client, _student_client


def _migration():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/c82e1d6b904a_selfcheck_homework.py"
    spec = importlib.util.spec_from_file_location("selfcheck_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(db, table):
    return list(db.execute(text(f"SELECT * FROM {table} ORDER BY id")).all())


def test_migration_preserves_all_works_and_progress_and_is_repeatable(db, user_factory):
    user = user_factory()
    now = datetime.now(timezone.utc)
    for tid in (162, 163):
        db.add(TrackerTask(id=tid, title="Self-check", kind="material"))
    db.flush()
    for bid, tid in _migration().TARGETS:
        db.add(TaskBlock(id=bid, task_id=tid, block_type="portfolio",
                         title="Самопроверка компоновки", portfolio_window_hours=800,
                         is_required=True, created_at=now))
    db.add(TaskBlock(id=400, task_id=162, block_type="portfolio", title="Other"))
    db.flush()
    db.add(TaskBlockState(block_id=378, user_id=user.id, status="done",
                          started_at=now - timedelta(days=1), completed_at=now,
                          completion_source="portfolio_upload"))
    db.add(TaskBlockState(block_id=381, user_id=user.id, status="open", started_at=now))
    db.add(Work(user_id=user.id, work_type="before", month="ноябрь", year=2026,
                filename="previous.jpg", s3_url="https://storage.test/previous.jpg", status="success"))
    db.commit()
    before = {t: _snapshot(db, t) for t in ("works", "task_block_states", "tracker_tasks")}
    migration = _migration()
    migration.convert_existing(db.connection())
    migration.convert_existing(db.connection())
    db.expire_all()
    for bid, tid in migration.TARGETS:
        block = db.get(TaskBlock, bid)
        assert (block.block_type, block.upload_button_label) == ("photo_upload", "Загрузить работы")
        assert block.task_id == tid and block.portfolio_window_hours == 800 and block.is_required
    assert db.get(TaskBlock, 400).block_type == "portfolio"
    assert before == {t: _snapshot(db, t) for t in before}
    assert db.query(TaskBlockSubmission).count() == 0  # No guessed ownership of old photos.


def test_student_keeps_completed_step_and_can_open_previous_files(client, db, user_factory, session_factory):
    student = _student_client(client, user_factory, session_factory)
    task = _material_task_with_blocks(db, student.id, blocks=[{
        "block_type": "photo_upload", "upload_button_label": "Загрузить работы",
    }])
    block = db.query(TaskBlock).filter_by(task_id=task.id).one()
    db.add(TaskBlockState(block_id=block.id, user_id=student.id, status="done",
                          completion_source="portfolio_upload"))
    db.commit()
    response = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks")
    assert response.status_code == 200, response.text
    payload = response.json()["blocks"][0]
    assert payload["done"] is True
    assert payload["upload_button_label"] == "Загрузить работы"
    assert payload["legacy_portfolio_url"] == "/cabinet/portfolio"
    assert payload["upload_endpoint"] == f"/cabinet/tracker/blocks/{block.id}/upload"


def test_editor_roundtrip_keeps_instruction_video_and_label(client, db, user_factory, session_factory):
    staff = _staff_client(client, user_factory, session_factory)
    video = LearningVideo(bunny_library_id=1, bunny_video_id="selfcheck-video", title="Instruction")
    db.add(video)
    db.commit()
    task = _material_task_with_blocks(db, staff.id, blocks=[{
        "block_type": "photo_upload", "video_id": video.id, "upload_button_label": "Загрузить работы",
    }])
    block = db.query(TaskBlock).filter_by(task_id=task.id).one()
    response = client.post(f"/cabinet/staff/program/items/{task.id}/material", json={
        "title": task.title, "blocks": [{"id": block.id, "block_type": "photo_upload", "body": "Edited"}],
    })
    assert response.status_code == 200, response.text
    db.refresh(block)
    assert block.video_id == video.id
    assert block.upload_button_label == "Загрузить работы"
    assert video.id in block_bound_video_ids(db)
    _student_client(client, user_factory, session_factory)
    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]
    assert payload["video_embed_endpoint"] == f"/cabinet/videos/{video.id}/embed"
    assert payload["done"] is False


def test_instruction_video_is_not_exposed_outside_its_homework(db, user_factory):
    student = user_factory()
    video = LearningVideo(bunny_library_id=1, bunny_video_id="restricted", title="Private", is_published=True)
    db.add(video)
    db.flush()
    task = TrackerTask(title="Private homework", kind="material", is_published=True, assign_to_all=False)
    db.add(task)
    db.flush()
    db.add(TaskBlock(task_id=task.id, block_type="photo_upload", video_id=video.id))
    db.commit()
    assert not is_video_accessible(db, video, {"user_id": student.id, "role_rank": 1, "tariff": student.tariff})


def test_new_upload_is_attached_only_to_selected_homework(client, db, user_factory, session_factory):
    from tests.test_required_photos import _post
    from app.models.task_block import TaskBlockSubmissionImage
    student = _student_client(client, user_factory, session_factory)
    task = _material_task_with_blocks(db, student.id, blocks=[
        {"block_type": "photo_upload", "title": "First", "is_required": True},
        {"block_type": "photo_upload", "title": "Second", "is_required": True},
    ])
    first, second = db.query(TaskBlock).filter_by(task_id=task.id).order_by(TaskBlock.sort_order).all()
    assert _post(client, first.id).status_code == 200
    submission = db.query(TaskBlockSubmission).filter_by(block_id=first.id, user_id=student.id).one()
    assert submission.submitted_at is not None
    assert db.query(TaskBlockSubmissionImage).filter_by(submission_id=submission.id).count() == 1
    assert db.query(TaskBlockSubmission).filter_by(block_id=second.id).count() == 0
    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"]
    assert payload[0]["done"] is True and payload[1]["done"] is False
