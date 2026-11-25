"""Контрольная на время после срока (владелец 30.09.2026).

«Чтобы мы видели просрочку по дедлайну, но при этом чтобы у него не
блокировалась отправка… сдать можно, но записать, что просрочен дедлайн.»

До этого дня срок сдачи запирал контрольную так же, как «Домашнее задание»:
после срока кнопки загрузки пропадали. Теперь первую сдачу контрольной
принимают и отмечают опозданием — у ученика, на экране проверки и в
статистике. Уже сданную работу после срока не заменить: иначе черновик,
сданный вовремя, прятал бы настоящую работу, сданную позже.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import (
    BLOCK_TIMED,
    BLOCK_UPLOAD,
    TaskBlock,
    TaskBlockState,
    TaskBlockSubmission,
    TaskBlockSubmissionImage,
)
from app.services import s3 as s3_service
from app.services.activity_stats import get_timed_stats
from app.services.cycle_feed import build_cycle_feed
from app.services.program import day_bounds
from app.services.review_aggregate import DOMAIN_BLOCK_WORK, student_review_items
from app.services.task_blocks import (
    get_state,
    start_timed_block,
    timed_seconds_left,
)
from app.services.tracker import create_task
from app.services.tz import msk_midnight, today_msk

FAKE_URL = "https://s3.example.com/zadaniya/control.jpg"
TODAY = today_msk()
CYCLE_START = TODAY - timedelta(days=1)
CYCLE_END = TODAY + timedelta(days=6)
YESTERDAY = day_bounds(TODAY - timedelta(days=1))[0]
# Срок «впереди» — через двое суток, а не завтра в полночь: SQLite в тестах
# теряет пояс, и `completed_at` (МСК) читается как UTC — вечером по Москве
# сдвиг в три часа перекидывал сдачу за «завтрашнюю полночь».
LATER = day_bounds(TODAY + timedelta(days=2))[0]


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner):
    topic = LearningTopic(
        title="Цикл с контрольной", opens_at=_utc(msk_midnight(CYCLE_START)),
        ends_at=_utc(msk_midnight(CYCLE_END) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    return topic


def _task(db, owner):
    task = create_task(
        db, title="Контрольная по рисунку", user_id=owner.id, kind="material",
        due_at=day_bounds(TODAY + timedelta(days=2))[0] + timedelta(hours=6),
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _block(db, task, *, block_type=BLOCK_TIMED, order=1, submit_until=None, minutes=75):
    block = TaskBlock(
        task_id=task.id, block_type=block_type, title="Локация с дверью",
        sort_order=order, is_required=True, submit_until=submit_until,
        time_limit_minutes=minutes if block_type == BLOCK_TIMED else None,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _post(client, block_id):
    files = [("photos", ("control.jpg", b"fake-bytes", "image/jpeg"))]
    with patch.object(s3_service, "upload_to_s3", return_value=FAKE_URL):
        return client.post(
            f"/cabinet/tracker/blocks/{block_id}/upload", files=files, data={"comment": ""},
        )


def _payload(client, task):
    return client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]


def test_finish_work_starts_upload_timer_without_submitting(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, minutes=45)
    state = start_timed_block(db, block=block, user_id=user.id)
    state.started_at = datetime.now(timezone.utc) - timedelta(minutes=44)
    db.commit()

    response = client.post(f"/cabinet/tracker/blocks/{block.id}/finish")
    assert response.status_code == 200
    first = response.json()["work_finished_at"]
    assert client.post(f"/cabinet/tracker/blocks/{block.id}/finish").json()["work_finished_at"] == first
    payload = _payload(client, task)
    assert payload["work_finished_at"] == first
    assert 295 <= payload["upload_time_left_seconds"] <= 300
    assert payload["done"] is False
    assert state.completed_at is None


def test_finish_requires_a_started_timed_block(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task)
    assert client.post(f"/cabinet/tracker/blocks/{block.id}/finish").status_code == 409
    other = _block(db, task, block_type=BLOCK_UPLOAD, order=2)
    assert client.post(f"/cabinet/tracker/blocks/{other.id}/finish").status_code == 404


def test_finish_cannot_bypass_block_opening(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task)
    state = start_timed_block(db, block=block, user_id=user.id)
    block.opens_at = datetime.now(timezone.utc) + timedelta(days=1)
    db.commit()
    assert client.post(f"/cabinet/tracker/blocks/{block.id}/finish").status_code == 403
    db.refresh(state)
    assert state.work_finished_at is None


def test_upload_grace_boundary_and_legacy_attempt(db, regular_user):
    from app.services.task_blocks import timed_overrun, timed_upload_overrun
    task = _task(db, regular_user)
    block = _block(db, task, minutes=45)
    start = datetime(2026, 11, 9, 8, tzinfo=timezone.utc)
    state = TaskBlockState(block_id=block.id, user_id=regular_user.id, started_at=start,
                           work_finished_at=start + timedelta(minutes=45),
                           completed_at=start + timedelta(minutes=50))
    assert timed_overrun(block, state) is False
    assert timed_upload_overrun(state) is False
    state.completed_at += timedelta(microseconds=1)
    assert timed_upload_overrun(state) is True
    state.work_finished_at = None
    assert timed_overrun(block, state) is True  # Старая сдача сохраняет прежнее измерение.
    assert timed_upload_overrun(state) is False


def test_school_timing_migration_preserves_existing_rows():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    path = Path(__file__).parents[1] / "alembic/versions/b72e8a4c190f_timed_work_finish.py"
    spec = importlib.util.spec_from_file_location("school_timing_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with create_engine("sqlite://").begin() as conn:
        conn.execute(text("CREATE TABLE task_block_states (id INTEGER PRIMARY KEY, completed_at DATETIME)"))
        conn.execute(text("INSERT INTO task_block_states VALUES (1, '2026-11-08 10:00:00')"))
        conn.execute(text("CREATE TABLE lesson_attendance_marks (id INTEGER PRIMARY KEY, attended BOOLEAN)"))
        conn.execute(text("CREATE TABLE task_blocks (id INTEGER PRIMARY KEY)"))
        migration.op = Operations(MigrationContext.configure(conn))
        migration.upgrade()
        assert conn.execute(text("SELECT completed_at, work_finished_at FROM task_block_states")).one() == ("2026-11-08 10:00:00", None)
        assert "lesson_ends_at" not in {c["name"] for c in inspect(conn).get_columns("task_blocks")}
        migration.downgrade()
        assert "work_finished_at" not in {c["name"] for c in inspect(conn).get_columns("task_block_states")}


def test_upload_delay_does_not_count_as_work_overrun(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, minutes=45)
    now = datetime.now(timezone.utc)
    state = start_timed_block(db, block=block, user_id=user.id)
    state.started_at = now - timedelta(minutes=52)
    state.work_finished_at = now - timedelta(minutes=7)  # Ровно 45 минут на работу.
    db.commit()

    assert _post(client, block.id).status_code == 200
    payload = _payload(client, task)
    assert payload["done"] is True
    assert payload["overrun"] is False
    assert payload["upload_overrun"] is True
    attempt = get_timed_stats(db)["attempts"][0]
    assert attempt["minutes"] == 45
    assert attempt["upload_minutes"] == 7
    assert attempt["upload_overrun"] is True


def test_finished_work_is_not_reported_as_still_running_over(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, minutes=45)
    now = datetime.now(timezone.utc)
    state = start_timed_block(db, block=block, user_id=user.id)
    state.started_at = now - timedelta(minutes=47)
    state.work_finished_at = now - timedelta(minutes=2)
    db.commit()
    stats = get_timed_stats(db)
    assert stats["attempts"][0]["status"] == "uploading"
    assert stats["blocks"][0]["running_over"] == 0


# ── приём после срока ───────────────────────────────────────────────────────

def test_timed_work_is_accepted_after_the_deadline(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, submit_until=YESTERDAY)
    start_timed_block(db, block=block, user_id=user.id)
    db.commit()

    assert _payload(client, task)["edit_reason"] is None
    assert _post(client, block.id).status_code == 200
    assert get_state(db, block_id=block.id, user_id=user.id).status == "done"


def test_task_level_deadline_does_not_lock_the_timed_work_either(auth_client, db):
    """«Контрольная по рисунку» на проде держит срок у задания, а не у блока."""
    client, user = auth_client
    task = _task(db, user)
    task.submit_until = YESTERDAY
    db.commit()
    block = _block(db, task)
    start_timed_block(db, block=block, user_id=user.id)
    db.commit()

    assert _post(client, block.id).status_code == 200


def test_late_work_is_marked_for_the_student(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, submit_until=YESTERDAY)
    start_timed_block(db, block=block, user_id=user.id)
    db.commit()

    before = _payload(client, task)
    assert before["late_allowed"] is True
    assert before["deadline_passed"] is True

    _post(client, block.id)
    after = _payload(client, task)
    assert after["done"] is True
    assert after["late"] is True


def test_work_in_time_is_not_marked_late(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, submit_until=LATER)
    start_timed_block(db, block=block, user_id=user.id)
    db.commit()
    _post(client, block.id)

    assert _payload(client, task)["late"] is False


def test_late_work_is_marked_on_the_review_screen(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, submit_until=YESTERDAY)
    start_timed_block(db, block=block, user_id=user.id)
    db.commit()
    _post(client, block.id)

    items = student_review_items(db, student_id=user.id, role_rank=5)
    work = next(i for i in items if i.domain == DOMAIN_BLOCK_WORK)
    assert "(сдано после срока)" in work.title


def test_submitted_work_cannot_be_replaced_after_the_deadline(auth_client, db):
    """Опоздание пишется по первой сдаче — заменить её после срока нельзя."""
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, submit_until=LATER)
    start_timed_block(db, block=block, user_id=user.id)
    db.commit()
    assert _post(client, block.id).status_code == 200
    block.submit_until = YESTERDAY
    db.commit()

    assert _post(client, block.id).status_code == 409
    image = db.query(TaskBlockSubmissionImage).first()
    assert client.post(
        f"/cabinet/tracker/blocks/{block.id}/images/{image.id}/delete"
    ).status_code == 409
    assert _payload(client, task)["edit_reason"]


def test_upload_block_follows_the_same_late_rule(auth_client, db):
    """С 06.10.2026 правило контрольной общее: «Домашнее задание» после срока
    тоже принимает первую сдачу (владелец: «досдать свыше срока всегда
    можно… ЭТО ПРАВИЛО!!!»). До этого его срок запирал (27.09.2026)."""
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, block_type=BLOCK_UPLOAD, submit_until=YESTERDAY)

    assert _post(client, block.id).status_code == 200


def test_closed_block_still_locks_the_timed_work(auth_client, db):
    """`closes_at` закрывает блок целиком — это не срок сдачи, и опозданием
    его не обойти."""
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task)
    block.closes_at = YESTERDAY
    db.commit()
    start_timed_block(db, block=block, user_id=user.id)
    db.commit()

    assert _post(client, block.id).status_code in (404, 409)


def test_expired_timed_block_keeps_holding_the_feed(db, regular_user):
    """Сдать контрольную можно всегда, поэтому развязка тупика по сроку к ней
    не применяется: следующий шаг ждёт сдачи."""
    _cycle(db, regular_user)
    task = _task(db, regular_user)
    _block(db, task, order=1, submit_until=YESTERDAY)
    db.add(TaskBlock(task_id=task.id, block_type="text", body="Что дальше", sort_order=2))
    db.commit()

    steps = build_cycle_feed(
        db, user_id=regular_user.id, user_tariff=regular_user.tariff,
        start=CYCLE_START, end=CYCLE_END,
    )

    assert [s["status"] for s in steps] == ["current", "locked"]


# ── обратный отсчёт ─────────────────────────────────────────────────────────

def test_seconds_left_counts_down_from_the_start(db, regular_user):
    task = _task(db, regular_user)
    block = _block(db, task, minutes=75)
    state = start_timed_block(db, block=block, user_id=regular_user.id)
    started = state.started_at.replace(tzinfo=timezone.utc) if state.started_at.tzinfo is None else state.started_at

    assert timed_seconds_left(block, state, now=started + timedelta(minutes=15)) == 60 * 60
    assert timed_seconds_left(block, state, now=started + timedelta(minutes=80)) == -5 * 60
    assert timed_seconds_left(block, None) is None


def test_feed_sends_the_time_left_once_started(auth_client, db):
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, minutes=75)

    assert _payload(client, task)["time_left_seconds"] is None
    state = start_timed_block(db, block=block, user_id=user.id)
    # Старт ставим в UTC явно: SQLite теряет пояс, и московское `_now()`
    # прочиталось бы на три часа позже (на проде колонка с поясом).
    state.started_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    db.commit()
    left = _payload(client, task)["time_left_seconds"]
    assert 64 * 60 < left <= 65 * 60


# ── статистика ──────────────────────────────────────────────────────────────

def test_stats_count_overrun_and_late(db, user_factory):
    task = _task(db, user_factory(vk_id=500_001, name="Преподаватель"))
    block = _block(db, task, submit_until=datetime(2026, 10, 3, 20, 0))
    start = datetime(2026, 10, 3, 18, 0)
    cases = {
        "Уложилась": (start, start + timedelta(minutes=60)),
        "Превысил": (start, start + timedelta(minutes=90)),
        "Опоздал": (start + timedelta(hours=3), start + timedelta(hours=3, minutes=30)),
        "Рисует": (datetime(2026, 9, 1, 10, 0), None),
    }
    for index, (name, (started, finished)) in enumerate(cases.items()):
        student = user_factory(vk_id=600_000 + index, name=name)
        db.add(TaskBlockState(
            block_id=block.id, user_id=student.id,
            status="done" if finished else "open",
            started_at=started, completed_at=finished,
        ))
    db.commit()

    stats = get_timed_stats(db)

    row = stats["blocks"][0]
    assert (row["started"], row["submitted"], row["in_time"]) == (4, 3, 2)
    assert (row["overrun"], row["late"], row["running_over"]) == (1, 1, 1)
    flagged = {r["name"]: r for r in stats["students"]}
    assert set(flagged) == {"Превысил", "Опоздал"}
    assert flagged["Превысил"]["overrun"] and not flagged["Превысил"]["late"]
    assert flagged["Превысил"]["minutes"] == 90
    assert flagged["Опоздал"]["late"] and not flagged["Опоздал"]["overrun"]


def test_stats_attempts_have_one_status_each_for_filters(db, user_factory):
    """Этап 4 статистики (Александрия 22.10.2026): каждая попытка с одним
    статусом — проценты в карточке складываются в 100. Опоздал и превысил
    сразу — «после срока»; уровень приходит от вызывающего."""
    task = _task(db, user_factory(vk_id=500_101, name="Преподаватель"))
    task.subject = "Рисунок"
    block = _block(db, task, submit_until=datetime(2026, 10, 3, 20, 0))
    start = datetime(2026, 10, 3, 18, 0)
    cases = {
        "Уложилась": (start, start + timedelta(minutes=60)),
        "Превысил": (start, start + timedelta(minutes=90)),
        "Опоздал и превысил": (start + timedelta(hours=3), start + timedelta(hours=4, minutes=30)),
        "Время вышло": (datetime(2026, 9, 1, 10, 0), None),
    }
    ids = {}
    for index, (name, (started, finished)) in enumerate(cases.items()):
        student = user_factory(vk_id=600_100 + index, name=name)
        ids[name] = student.id
        db.add(TaskBlockState(
            block_id=block.id, user_id=student.id,
            status="done" if finished else "open",
            started_at=started, completed_at=finished,
        ))
    db.commit()

    stats = get_timed_stats(db, levels={ids["Уложилась"]: 2})

    by_name = {a["name"]: a for a in stats["attempts"]}
    assert [a["name"] for a in stats["attempts"]] == sorted(cases)
    assert {name: a["status"] for name, a in by_name.items()} == {
        "Уложилась": "in_time", "Превысил": "overrun",
        "Опоздал и превысил": "late", "Время вышло": "running_over",
    }
    assert by_name["Уложилась"]["level"] == 2 and by_name["Превысил"]["level"] is None
    assert by_name["Превысил"]["minutes"] == 90 and by_name["Превысил"]["limit"] == 75
    assert by_name["Уложилась"]["subject"] == "Рисунок"
    assert by_name["Уложилась"]["month"] == "Без месяца"


def test_stats_distinguish_timer_results_and_zero_score(db, user_factory):
    student = user_factory(vk_id=600_150, name="Ученик")
    task = _task(db, user_factory(vk_id=500_150, name="Преподаватель"))
    started = datetime(2026, 10, 3, 18, 0)
    stopped = started + timedelta(minutes=60)
    uploaded = stopped + timedelta(minutes=3)
    drawing = _block(db, task, order=1, minutes=60, submit_until=stopped)
    composition = _block(db, task, order=2, minutes=60)
    db.add_all([
        TaskBlockState(block_id=drawing.id, user_id=student.id, started_at=started,
                       work_finished_at=stopped, completed_at=uploaded),
        TaskBlockState(block_id=composition.id, user_id=student.id, completed_at=uploaded),
        TaskBlockSubmission(block_id=drawing.id, user_id=student.id, submitted_at=uploaded, score=0),
        TaskBlockSubmission(block_id=composition.id, user_id=student.id, submitted_at=uploaded),
    ])
    db.commit()

    scored, awaiting = sorted(get_timed_stats(db)["attempts"], key=lambda row: row["score"] is None)
    assert scored["submitted"] and scored["score"] == 0
    assert scored["timer_result"] == "in_time" and scored["status"] == "late"
    assert awaiting["submitted"] and awaiting["score"] is None
    assert awaiting["timer_result"] is None and awaiting["status"] == "unknown_timer"


def test_condition_is_hidden_until_the_start(auth_client, db):
    """Условие контрольной до «Начать работу» не уходит в браузер вовсе
    (владелец 02.10.2026): иначе ученик успевает обдумать задание до
    таймера. Прятать только на экране мало — текст был бы в ответе."""
    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, minutes=75)
    block.body = "Нарисуйте локацию с дверью"
    db.commit()

    before = _payload(client, task)
    assert before["body"] is None
    assert before["body_html"] is None
    assert before["body_hidden"] is True
    assert "локацию с дверью" not in client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").text

    start_timed_block(db, block=block, user_id=user.id)
    db.commit()
    after = _payload(client, task)
    assert after["body"] == "Нарисуйте локацию с дверью"
    assert "локацию с дверью" in after["body_html"]
    assert "body_hidden" not in after


def test_condition_photos_are_hidden_until_the_start(auth_client, db):
    """Фото к условию контрольной (владелец 02.10.2026) — по тому же правилу,
    что и текст: до «Начать работу» их адресов в ответе нет."""
    from app.models.task_block import TaskBlockImage

    client, user = auth_client
    task = _task(db, user)
    block = _block(db, task, minutes=75)
    db.add(TaskBlockImage(
        block_id=block.id, image_s3_url="https://example.com/still-life.jpg", sort_order=0,
    ))
    db.commit()

    assert "images" not in _payload(client, task)
    assert "still-life.jpg" not in client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").text

    start_timed_block(db, block=block, user_id=user.id)
    db.commit()
    assert _payload(client, task)["images"] == [{"url": "https://example.com/still-life.jpg"}]
