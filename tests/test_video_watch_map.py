"""Карта просмотра: ученик видит, какие куски ролика не просмотрены и что
досмотреть (владелец 09.10.2026).

Прецедент — прод 09.10.2026, ученица id 202: ролик 18:18, пропущен кусок
1:07–1:40, до зачёта не хватало 4 секунд. Кружок 16 раз ответил «досмотри
до конца», и она перематывала в конец. Теперь карту прохода
(`video_progress.watch_map`) получают плеер (со страницей и с каждым ответом
на отметку), кружок (в отказе) и карточка ученика у сотрудников."""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.video_progress import VideoProgress
from app.models.video_watch_segment import VideoWatchSegment
from app.services.video_progress import (
    get_video_progress,
    load_watch_map,
    merge_segments,
    watch_map,
)

ROOT = Path(__file__).resolve().parents[1]
VIDEO = "87686c97-b679-48d8-921b-668d7a12002d"
# Отрезки ученицы id 202 на проде 09.10.2026, 10:38 UTC (округлены).
PROD_SEGMENTS = [
    (0.0, 6.5), (0.0, 35.6), (30.6, 39.5), (34.5, 67.8),
    (100.4, 109.4), (110.5, 735.5), (735.5, 1016.2), (1016.2, 1098.3),
]


def _seed(db, user_id, segments, *, duration=1098.0, completed=False, video=VIDEO):
    db.add(VideoProgress(
        user_id=user_id, video_id=video, position_seconds=0.0, duration_seconds=duration,
        watched_seconds=1100.0, covered_seconds=0.0,
        completed_at=datetime.now(timezone.utc) if completed else None,
    ))
    for start, end in segments:
        db.add(VideoWatchSegment(
            user_id=user_id, video_id=video, session_id="s", start_seconds=start, end_seconds=end,
        ))
    db.commit()


def test_map_names_the_hole_of_prod_case():
    merged = merge_segments(PROD_SEGMENTS, duration_seconds=1098.0)
    result = watch_map(merged, 1098.0)
    assert result["threshold"] == 1068.0
    assert result["covered"] == pytest.approx(1064.3, abs=0.2)
    # Вторая дырка — секундный срез сети: длиннее склейки в 1 с.
    assert result["gaps"] == [[67.8, 100.4], [109.4, 110.5]]


def test_map_lists_tail_and_holes_in_order():
    merged = merge_segments([(0, 100), (200, 300)], duration_seconds=600.0)
    assert watch_map(merged, 600.0)["gaps"] == [[100.0, 200.0], [300.0, 600.0]]


def test_load_map_is_empty_for_untouched_and_credited(db, regular_user):
    assert load_watch_map(db, None) is None
    _seed(db, regular_user.id, [(0.0, 100.0)], completed=True)
    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO)
    assert load_watch_map(db, progress) is None


def test_load_map_reads_position_independent_state(db, regular_user):
    """Позиция 0 (ученица перемотала в начало) — карта та же: подсказка
    больше не зависит от того, где ученик стоит."""
    _seed(db, regular_user.id, PROD_SEGMENTS)
    progress = get_video_progress(db, user_id=regular_user.id, video_id=VIDEO)
    assert load_watch_map(db, progress)["gaps"] == [[67.8, 100.4], [109.4, 110.5]]


def test_progress_answer_carries_map_and_drops_it_on_credit(auth_client, db, monkeypatch):
    """Отметка возвращает карту после себя; на зачёте карты нет — плеер
    прячет полоску."""
    from tests.test_routes_video import VIDEO_ID, _configure_bunny, seed_video_watch

    client, user = auth_client
    _configure_bunny(monkeypatch)
    seed_video_watch(db, user_id=user.id, position=300.0, covered=300.0, duration=600.0)

    body = client.post("/cabinet/video/progress", json={
        "position_seconds": 305.0, "duration_seconds": 600.0, "playback_active": True,
    }).json()
    assert body["completed"] is False
    assert body["watch_map"]["gaps"][0][1] == 600.0

    db.query(VideoProgress).filter_by(user_id=user.id, video_id=VIDEO_ID).update(
        {"covered_seconds": 600.0})
    db.query(VideoWatchSegment).filter_by(user_id=user.id, video_id=VIDEO_ID).update(
        {"end_seconds": 600.0})
    db.commit()
    body = client.post("/cabinet/video/progress", json={
        "position_seconds": 600.0, "duration_seconds": 600.0, "ended": True,
    }).json()
    assert body["completed"] is True
    assert body["watch_map"] is None


def test_embed_gives_player_the_map(auth_client, db, monkeypatch):
    from tests.test_routes_video import seed_video_watch
    from tests.test_routes_video_embed import _configure_bunny, _video_open_to_all

    client, user = auth_client
    _configure_bunny(monkeypatch)
    video = _video_open_to_all(db, user.id)
    assert client.get(f"/cabinet/videos/{video.id}/embed").json()["watch_map"] is None

    seed_video_watch(db, user_id=user.id, video_id=video.bunny_video_id,
                     position=10.0, covered=200.0, duration=600.0)
    body = client.get(f"/cabinet/videos/{video.id}/embed").json()
    assert body["watch_map"]["gaps"] == [[200.0, 600.0]]


def test_circle_refusal_carries_the_map(client, db, user_factory, session_factory, monkeypatch):
    from tests.test_routes_task_blocks import _student_client, _video_task_with_block

    monkeypatch.setattr("app.api.cabinet_tracker.VIDEO_WATCH_CONTROL_ENABLED", True)
    monkeypatch.setattr("app.services.video_catalog.settings.bunny_stream_enabled", True)
    staff = user_factory(vk_id=550_901, name="Стафф", is_admin=True, role_name="админ")
    _task, block, video = _video_task_with_block(db, staff.id)
    student = _student_client(client, user_factory, session_factory, vk_id=550_902)
    _seed(db, student.id, [(0.0, 30.0), (60.0, 120.0)], duration=120.0,
          video=video.bunny_video_id)

    resp = client.post(f"/cabinet/tracker/blocks/{block.id}/watched")
    assert resp.status_code == 409
    assert resp.json()["error"] == "not_watched"
    assert resp.json()["watch_map"]["gaps"] == [[30.0, 60.0]]


def test_student_card_lists_missing_parts(db, user_factory):
    from app.models.learning_video import LearningVideo
    from app.services.activity_stats import get_video_watch_stats

    student = user_factory(vk_id=980_901, name="Ученица", role_name="ученик")
    db.add(LearningVideo(title="Метод", bunny_library_id=1, bunny_video_id=VIDEO,
                         status="published", duration_seconds=1098.0))
    _seed(db, student.id, PROD_SEGMENTS)

    [v] = get_video_watch_stats(db, student_id=student.id)["videos"]
    assert v["missing"] == ["1:07–1:40", "1:49–1:50"]


@pytest.mark.skipif(shutil.which("node") is None, reason="нет Node.js")
def test_gaps_advice_texts_in_node():
    """Настоящий `video-player.js` в Node: тексты подсказки по карте прохода."""
    script = """
    global.window = { addEventListener() {} };
    global.document = { addEventListener() {}, visibilityState: 'visible' };
    require(%s);
    const a = window.lrnVideoPlayer.gapsAdvice;
    const out = {
      prod: a({duration: 1098, threshold: 1068, covered: 1064.4, gaps: [[67.8, 100.4]]}),
      linear: a({duration: 600, threshold: 570, covered: 200, gaps: [[200, 600]]}),
      both: a({duration: 600, threshold: 570, covered: 400, gaps: [[100, 150], [450, 600]]}),
      enough: a({duration: 600, threshold: 570, covered: 580, gaps: [[100, 110]]}),
      none: a(null),
      // Прод 11.10.2026, ученик id 265: 19 секунд просмотра, ползунок в конце.
      skipped: a({duration: 5475, threshold: 5445, covered: 19, gaps: [[19, 5475]]}, 5475),
      watching: a({duration: 600, threshold: 570, covered: 200, gaps: [[200, 600]]}, 205),
    };
    console.log(JSON.stringify(out));
    """ % json.dumps(str(ROOT / "app/static/js/video-player.js"))
    out = json.loads(subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout)

    assert out["prod"]["jumpTo"] == pytest.approx(62.8)

    assert out["linear"]["jumpTo"] is None

    assert out["skipped"]["jumpTo"] == 14
    assert out["watching"]["jumpTo"] is None

    assert out["both"]["jumpTo"] == 95

    assert out["enough"]["jumpTo"] == 572
    assert out["none"] is None
