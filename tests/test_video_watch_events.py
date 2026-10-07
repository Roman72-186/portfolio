"""Статистика незачёта видео (владелец 07.10.2026): причина каждого среза и
отказа кружка хранится в `video_watch_events` и показывается в карточке
ученика и на «Статистике активности».

Классификатор проверяется на настоящих строках журнала прода 06.10.2026."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.learning_video import LearningVideo
from app.models.video_progress import VideoProgress
from app.models.video_watch_event import VideoWatchEvent
from app.services.video_watch_events import (
    CUT_INSIDE_CREDITED,
    CUT_NETWORK,
    CUT_PAUSED_PLAYING,
    CUT_RESUME,
    CUT_SEEK,
    KIND_CUT,
    KIND_REFUSAL,
    REFUSAL_BELOW_THRESHOLD,
    REFUSAL_SHORTFALL,
    classify_cut,
)

VIDEO = "11111111-2222-3333-4444-555555555555"


def _cut(position_from, position_to, skipped, gap, playing, watched_after):
    """Строка журнала → классификатор. «Засчитано всего» в журнале — после
    шага; до шага было на засчитанный прирост меньше."""
    credited_step = (position_to - position_from) - skipped
    return classify_cut(
        position_from=position_from, position_to=position_to, skipped_seconds=skipped,
        gap_seconds=gap, playing=playing, credited_before=watched_after - credited_step,
    )


# ── Классификатор на строках прода 06.10.2026 ───────────────────────────────

def test_player_said_paused_while_video_played():
    """Ученица id 229: «позиция 10→20 | срезано=10 | пауза=10 | играло=False»
    сто раз подряд — ролик шёл, флаг врал."""
    assert _cut(10, 20, 10, 10, False, 3) == CUT_PAUSED_PLAYING
    # Первая отметка после семи часов простоя — не «ролик шёл», а старт
    # плеера с начала: 9 секунд внутри засчитанного запаса.
    assert _cut(1, 10, 9, 24714, False, 3) == CUT_RESUME


def test_small_cut_while_playing_is_network():
    """«35→45 | срезано=5 | пауза=0 | играло=True» — пачка запросов за секунду."""
    assert _cut(35, 45, 5, 0, True, 36) == CUT_NETWORK


def test_jump_from_zero_into_credited_is_resume():
    """«0→576 | срезано=576 | пауза=5 | играло=False | засчитано=599» — плеер
    стартовал с нуля и прыгнул на место остановки."""
    assert _cut(0, 576, 576, 5, False, 599) == CUT_RESUME
    assert _cut(0, 116, 110, 0, True, 121) == CUT_RESUME


def test_jump_inside_credited_is_not_a_loss():
    """«97→955 | срезано=859 | играло=False | засчитано=1062» — перемотка по
    уже просмотренному или второй плеер: секунды засчитаны раньше."""
    assert _cut(97, 955, 859, 1, False, 1062) == CUT_INSIDE_CREDITED


def test_jump_beyond_credited_is_seek():
    assert _cut(10, 300, 285, 2, True, 15) == CUT_SEEK
    # Перемотка на паузе: позиция ушла быстрее, чем ролик мог проиграть.
    assert _cut(10, 300, 290, 30, False, 10) == CUT_SEEK


# ── Запись ───────────────────────────────────────────────────────────────────

def _student(user_factory, vk_id=980_001, name="Ученица"):
    return user_factory(vk_id=vk_id, name=name, role_name="ученик")


def _event(db, user, reason, *, kind=KIND_CUT, skipped=60.0, video=VIDEO, at=None, **extra):
    event = VideoWatchEvent(
        user_id=user.id, video_id=video, kind=kind, reason=reason,
        skipped_seconds=skipped if kind == KIND_CUT else None,
        created_at=at or datetime.now(timezone.utc), **extra,
    )
    db.add(event)
    return event


def test_skip_forward_is_stored_with_reason(auth_client, db, monkeypatch):
    """Срез пишется в базу — до 07.10.2026 он был только строкой журнала."""
    from tests.test_routes_video import VIDEO_ID, _configure_bunny

    client, user = auth_client
    _configure_bunny(monkeypatch)
    db.add(VideoProgress(user_id=user.id, video_id=VIDEO_ID, position_seconds=10.0, watched_seconds=10.0))
    db.commit()

    resp = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": 300, "duration_seconds": 600, "playback_active": True},
    )

    assert resp.status_code == 200
    [event] = db.query(VideoWatchEvent).all()
    assert (event.user_id, event.video_id, event.kind, event.reason) == (user.id, VIDEO_ID, KIND_CUT, CUT_SEEK)
    assert (event.position_from, event.position_to, event.playing) == (10.0, 300.0, True)
    assert event.skipped_seconds > 250


def test_event_failure_does_not_lose_progress(auth_client, db, monkeypatch):
    """Сбой записи статистики не роняет сохранение прогресса и ответ."""
    from sqlalchemy.exc import SQLAlchemyError

    from app.services import video_watch_events
    from tests.test_routes_video import VIDEO_ID, _configure_bunny

    client, user = auth_client
    _configure_bunny(monkeypatch)
    db.add(VideoProgress(user_id=user.id, video_id=VIDEO_ID, position_seconds=10.0, watched_seconds=10.0))
    db.commit()
    real_add = video_watch_events.DBSession.add

    def broken_add(self, obj, *a, **kw):
        if isinstance(obj, VideoWatchEvent):
            raise SQLAlchemyError("boom")
        return real_add(self, obj, *a, **kw)

    monkeypatch.setattr(video_watch_events.DBSession, "add", broken_add)
    resp = client.post(
        "/cabinet/video/progress",
        json={"position_seconds": 300, "duration_seconds": 600, "playback_active": True},
    )
    monkeypatch.undo()

    assert resp.status_code == 200
    db.expire_all()
    assert db.get(VideoProgress, (user.id, VIDEO_ID)).position_seconds == 300.0
    assert db.query(VideoWatchEvent).count() == 0


def test_refusal_is_stored_with_block_and_reason(client, db, user_factory, session_factory, watch_control_on):
    from tests.test_routes_task_blocks import _student_client, _video_task_with_block

    staff = user_factory(vk_id=980_090, name="Стафф", is_admin=True, role_name="админ")
    _task, block, video = _video_task_with_block(db, staff.id)
    student = _student_client(client, user_factory, session_factory)
    db.add(VideoProgress(
        user_id=student.id, video_id=video.bunny_video_id,
        position_seconds=115.0, watched_seconds=40.0, duration_seconds=120.0,
    ))
    db.commit()

    assert client.post(f"/cabinet/tracker/blocks/{block.id}/watched").status_code == 409

    [event] = db.query(VideoWatchEvent).all()
    assert (event.kind, event.reason, event.block_id) == (KIND_REFUSAL, REFUSAL_SHORTFALL, block.id)
    assert (event.position_to, event.watched_seconds) == (115.0, 40.0)


@pytest.fixture()
def watch_control_on(monkeypatch):
    from app.api import cabinet_tracker

    monkeypatch.setattr(cabinet_tracker, "VIDEO_WATCH_CONTROL_ENABLED", True)


# ── Карточка ученика ─────────────────────────────────────────────────────────

def _video(db, title="Перспектива", duration=600.0):
    video = LearningVideo(
        title=title, bunny_library_id=1, bunny_video_id=VIDEO, status="published",
        duration_seconds=duration,
    )
    db.add(video)
    return video


def test_student_details_split_losses_from_harmless(db, user_factory):
    from app.services.activity_stats import get_video_watch_stats

    student = _student(user_factory)
    _video(db)
    db.add(VideoProgress(user_id=student.id, video_id=VIDEO, position_seconds=580.0,
                         watched_seconds=300.0, duration_seconds=600.0))
    _event(db, student, CUT_PAUSED_PLAYING, skipped=120.0)
    _event(db, student, CUT_SEEK, skipped=60.0)
    _event(db, student, CUT_RESUME, skipped=400.0)
    _event(db, student, REFUSAL_SHORTFALL, kind=KIND_REFUSAL, block_id=7,
           position_to=580.0, watched_seconds=300.0, duration_seconds=600.0)
    db.commit()

    [v] = get_video_watch_stats(db, student_id=student.id)["videos"]

    assert v["title"] == "Перспектива"
    assert (v["watch_state"], v["credited_seconds"], v["needed_seconds"]) == ("watching", 300, 570)
    assert v["lost_seconds"] == 180
    assert [(l["label"], l["seconds"]) for l in v["losses"]] == [
        ("Плеер считал паузой", 120), ("Перемотка", 60),
    ]
    assert v["harmless_seconds"] == 400
    assert v["refusals"] == 1
    assert v["last_refusal"]["text"] == "Дошёл до конца, но не хватило 4:30 честного просмотра"
    assert len(v["history"]) == 4
    assert any(e["harmless"] for e in v["history"])


def test_refusal_without_start_still_lists_video(db, user_factory):
    """Кружок отказал, а ролик ученик не запускал — строка ролика всё равно есть."""
    from app.services.activity_stats import get_video_watch_stats

    student = _student(user_factory)
    _video(db)
    _event(db, student, REFUSAL_BELOW_THRESHOLD, kind=KIND_REFUSAL, block_id=3,
           position_to=176.0, watched_seconds=171.0, duration_seconds=399.0)
    db.commit()

    [v] = get_video_watch_stats(db, student_id=student.id)["videos"]

    assert v["watch_state"] == "not_started"
    assert v["last_refusal"]["text"] == "Не досмотрел до конца: дошёл до 2:56, нужно до 6:09"


def test_school_stats_have_no_per_student_fields(db, user_factory):
    from app.services.activity_stats import get_video_watch_stats

    student = _student(user_factory)
    _video(db)
    db.add(VideoProgress(user_id=student.id, video_id=VIDEO, position_seconds=10.0, watched_seconds=10.0))
    db.commit()

    [v] = get_video_watch_stats(db)["videos"]
    assert "losses" not in v and "history" not in v


@pytest.fixture()
def people(user_factory):
    curator = user_factory(vk_id=980_101, name="Куратор", role_name="куратор")
    chief = user_factory(vk_id=980_102, name="Главный", is_admin=True, role_name="админ")
    student = _student(user_factory, vk_id=980_103)
    student.curator_id = curator.id
    return {"curator": curator, "chief": chief, "student": student}


def _statistics(client, session_factory, viewer, student):
    client.cookies.set("session_id", session_factory(viewer).id)
    resp = client.get(f"/cabinet/students/{student.id}/statistics")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_chief_sees_video_losses_in_card(client, db, session_factory, people):
    _video(db)
    _event(db, people["student"], CUT_SEEK, skipped=90.0)
    db.commit()

    [v] = _statistics(client, session_factory, people["chief"], people["student"])["school"]["videos"]
    assert v["lost_seconds"] == 90


def test_curator_card_has_no_loss_details(client, db, session_factory, people):
    """Права не менялись: разбор по роликам — рангу ≥ 4, куратору плитки."""
    _video(db)
    _event(db, people["student"], CUT_SEEK)
    db.commit()

    assert _statistics(client, session_factory, people["curator"], people["student"])["school"] is None


def test_card_queries_do_not_grow_with_events(client, db, session_factory, people, sql_counter):
    _video(db)
    _event(db, people["student"], CUT_SEEK)
    _event(db, people["student"], REFUSAL_SHORTFALL, kind=KIND_REFUSAL, block_id=1, duration_seconds=600.0)
    db.commit()
    _statistics(client, session_factory, people["chief"], people["student"])

    def _count():
        with sql_counter() as c:
            client.get(f"/cabinet/students/{people['student'].id}/statistics")
        return c.count

    before = _count()
    for i in range(30):
        _event(db, people["student"], CUT_NETWORK if i % 2 else CUT_INSIDE_CREDITED, skipped=5.0)
        _event(db, people["student"], REFUSAL_BELOW_THRESHOLD, kind=KIND_REFUSAL, block_id=1,
               duration_seconds=600.0, video=f"vid-{i}")
    db.commit()

    assert _count() == before


# ── «Статистика активности» ─────────────────────────────────────────────────

def test_loss_stats_by_reason_day_and_student(db, user_factory):
    from app.services.activity_stats import get_video_loss_stats

    student = _student(user_factory)
    student.tg_username = "lena_draws"
    quiet = _student(user_factory, vk_id=980_002, name="Тихий")
    staff = user_factory(vk_id=980_003, name="Куратор", role_name="куратор")
    _event(db, student, CUT_PAUSED_PLAYING, skipped=600.0)
    _event(db, student, CUT_SEEK, skipped=120.0)
    _event(db, student, CUT_INSIDE_CREDITED, skipped=900.0)
    _event(db, student, REFUSAL_SHORTFALL, kind=KIND_REFUSAL)
    _event(db, quiet, CUT_NETWORK, skipped=5.0)
    _event(db, staff, CUT_SEEK, skipped=6000.0)  # сотрудник — вне учёта
    _event(db, student, CUT_SEEK, skipped=6000.0, at=datetime.now(timezone.utc) - timedelta(days=9))
    db.commit()

    stats = get_video_loss_stats(db)

    assert stats["totals"] == {
        "lost": 12, "paused_playing": 10, "seek": 2, "network": 0, "harmless": 15, "refusals": 1,
    }
    assert len(stats["by_day"]) == 1
    # Тихий потерял 5 секунд и без отказов — в список «у кого больше всего» не попал.
    [row] = stats["students"]
    assert (row["id"], row["username"], row["lost"], row["refusals"]) == (student.id, "@lena_draws", 12, 1)
    assert row["url"] == f"/cabinet/students?student={student.id}&tab=statistics"


def test_activity_page_shows_loss_card(client, db, user_factory, session_factory):
    student = _student(user_factory)
    _event(db, student, CUT_PAUSED_PLAYING, skipped=600.0)
    superadmin = user_factory(vk_id=980_200, name="Супер", role_name="суперадмин")
    db.commit()
    client.cookies.set("session_id", session_factory(superadmin).id)

    html = client.get("/cabinet/superadmin/activity").text

    assert "Незачёт видео" in html
    assert "Плеер считал паузой" in html
    assert f"/cabinet/students?student={student.id}&amp;tab=statistics" in html


# ── Перенос журнала ─────────────────────────────────────────────────────────

def test_backfill_parses_journal_lines():
    """Строки журнала прода 06.10.2026 → события с причиной и временем журнала."""
    from scripts.backfill_video_watch_events import parse

    lines = [
        "2026-10-06T14:52:40+0000 adelene apparchi-app[1259]: Видео: кусок не засчитан | user=229"
        " | video=v1 | позиция 10→20 | срезано=10 | пауза между отметками=10 | играло=False"
        " | засчитано всего=3",
        "2026-10-06T22:07:49+0000 adelene apparchi-app[1259]: Видео не засчитано, кружок не поставлен"
        " | block=114 | user=253 | причина=не досмотрел до порога 369 | позиция=176 | честных=171"
        " | длительность=399",
        "2026-10-06T22:08:00+0000 adelene apparchi-app[1259]: Видео не засчитано, кружок не поставлен"
        " | block=999 | user=253 | причина=ролик не запускался",  # блок удалён — пропуск
    ]

    cut, refusal = parse(lines, {114: "v2"})

    assert (cut.user_id, cut.reason, cut.created_at.hour) == (229, CUT_PAUSED_PLAYING, 14)
    assert (refusal.block_id, refusal.video_id, refusal.reason) == (114, "v2", REFUSAL_BELOW_THRESHOLD)
    assert (refusal.position_to, refusal.watched_seconds, refusal.duration_seconds) == (176, 171, 399)
