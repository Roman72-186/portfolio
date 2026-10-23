"""«Ученики поимённо»: «Теория», выбор «месяц → цикл», уровень и группа
куратора (Александрия 22.10.2026).

«Теория» считается тем же `_assignment_activity`, что «Практика»: видео
посмотрел — видео-блок закрыт. Куратор видит ту же карточку только по своим
ученикам (`/cabinet/curator/statistics`).
"""
import re
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from app.constants import TARIFF_CONFIDENT_MAX, TARIFF_SELF
from app.models.learning_topic import LearningTopic
from app.models.learning_video import LearningVideo
from app.models.task_block import BLOCK_VIDEO, TaskBlock, TaskBlockState
from app.models.tracker import TrackerTask
from app.services.staff_dashboard import get_student_activity_overview

OPENS = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _cycle_with_video(db, *, stage_title="Октябрь", video_title="Перспектива"):
    stage = LearningTopic(title=stage_title, kind="stage", opens_at=OPENS,
                          is_published=True, assign_to_all=True)
    db.add(stage)
    db.flush()
    cycle = LearningTopic(title="", kind="week", parent_id=stage.id, opens_at=OPENS,
                          is_published=True, assign_to_all=True)
    db.add(cycle)
    db.flush()
    video = LearningVideo(bunny_library_id=1, bunny_video_id=f"vid-{cycle.id}", title=video_title)
    db.add(video)
    db.flush()
    task = TrackerTask(title="Теория недели", topic_id=cycle.id, kind="material", is_published=True)
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type=BLOCK_VIDEO, video_id=video.id)
    upload = TaskBlock(task_id=task.id, block_type="photo_upload", title="Эскизы", sort_order=1)
    db.add_all([block, upload])
    db.commit()
    return stage, cycle, block, upload


def test_theory_lists_who_watched_video_with_date(db, user_factory):
    watched = user_factory(vk_id=820_001, name="Watched", tariff=TARIFF_SELF)
    missed = user_factory(vk_id=820_002, name="Missed", tariff=TARIFF_CONFIDENT_MAX)
    stage, cycle, block, upload = _cycle_with_video(db)
    db.add(TaskBlockState(block_id=block.id, user_id=watched.id, status="done",
                          completed_at=datetime(2026, 10, 20, 11, 30, tzinfo=timezone.utc)))
    db.commit()

    overview = get_student_activity_overview(db, include_assignments=True)
    video = overview["videos"][0]

    assert video["id"] == block.id
    assert video["label"] == "Перспектива · Теория недели"
    assert video["topic_id"] == cycle.id
    assert set(video["eligible"]) == {watched.id, missed.id}
    assert video["submitted"] == [watched.id]
    assert video["pending"] == [missed.id]
    assert video["notes"] == {watched.id: "Посмотрел", missed.id: "Не посмотрел"}
    assert video["done_at"] == {watched.id: "20.10.2026 в 14:30"}
    # Видео в «Практику» не попадает, работа — в «Теорию».
    assert [item["id"] for item in overview["assignments"]] == [upload.id]
    assert overview["months"] == [{"id": stage.id, "label": "Октябрь",
                                   "cycles": [{"id": cycle.id, "label": "Цикл 1"}]}]


def test_practice_item_carries_cycle_and_submit_time(db, user_factory):
    done = user_factory(vk_id=820_010, name="Done", tariff=TARIFF_SELF)
    _stage, cycle, _block, upload = _cycle_with_video(db)
    db.add(TaskBlockState(block_id=upload.id, user_id=done.id, status="done",
                          completed_at=datetime(2026, 10, 21, 9, 5, tzinfo=timezone.utc)))
    db.commit()

    item = get_student_activity_overview(db, include_assignments=True)["assignments"][0]

    assert item["topic_id"] == cycle.id
    # Подпись не меняется, дата — отдельным полем: по подписи карточка
    # ученика отличает опоздание (здесь — после конца цикла).
    assert item["notes"][done.id] == "Сдал после срока"
    assert item["done_at"][done.id] == "21.10.2026 в 12:05"


def test_overview_for_curator_has_only_own_students(db, user_factory):
    curator = user_factory(vk_id=820_020, name="Curator", role_name="куратор")
    own = user_factory(vk_id=820_021, name="Own", tariff=TARIFF_CONFIDENT_MAX)
    own.curator_id = curator.id
    other = user_factory(vk_id=820_022, name="Other", tariff=TARIFF_CONFIDENT_MAX)
    db.commit()
    _cycle_with_video(db)

    overview = get_student_activity_overview(db, include_assignments=True, curator_id=curator.id)

    assert [row["id"] for row in overview["students"]] == [own.id]
    assert overview["videos"][0]["eligible"] == [own.id]
    assert other.id not in overview["videos"][0]["eligible"]
    assert overview["tariffs"] == [TARIFF_CONFIDENT_MAX]
    assert overview["students"][0]["level"] is None


def test_overview_for_curator_without_students_has_same_shape(db, user_factory):
    curator = user_factory(vk_id=820_025, name="Empty", role_name="куратор")

    overview = get_student_activity_overview(db, include_assignments=True, curator_id=curator.id)

    assert overview["students"] == [] and overview["tariffs"] == [] and overview["months"] == []


@pytest.fixture()
def curator_client(client, user_factory, session_factory):
    curator = user_factory(vk_id=820_030, name="Curator", role_name="куратор")
    client.cookies.set("session_id", session_factory(curator).id)
    return client, curator


def test_curator_statistics_page_shows_own_group(curator_client, db, user_factory):
    client, curator = curator_client
    own = user_factory(vk_id=820_031, name="Own", tariff=TARIFF_CONFIDENT_MAX)
    own.last_name, own.first_name = "Своя", "Ученица"
    own.curator_id = curator.id
    other = user_factory(vk_id=820_032, name="Other", tariff=TARIFF_CONFIDENT_MAX)
    other.last_name, other.first_name = "Чужая", "Ученица"
    db.commit()
    _cycle_with_video(db)

    resp = client.get("/cabinet/curator/statistics")

    assert resp.status_code == 200
    assert "Своя Ученица" in resp.text
    assert "Чужая Ученица" not in resp.text
    assert 'data-sbn-mode="theory"' in resp.text
    # Пункт меню «Статистика» ведёт сюда.
    assert 'href="/cabinet/curator/statistics"' in resp.text


def test_curator_statistics_closed_with_statistics_section(curator_client, db):
    """Раздел «Статистика» в «Доступах» закрывает и эту страницу."""
    from app.models.section_access import SectionAccessRule
    from app.services.section_access import LEVEL_NONE

    client, curator = curator_client
    db.add(SectionAccessRule(section_key="statistics", user_id=curator.id, level=LEVEL_NONE))
    db.commit()

    resp = client.get("/cabinet/curator/statistics", follow_redirects=False)

    assert resp.status_code == 403


def test_chief_teacher_goes_to_school_statistics(client, user_factory, session_factory):
    head = user_factory(vk_id=820_040, name="Head", role_name="админ")
    client.cookies.set("session_id", session_factory(head).id)

    resp = client.get("/cabinet/curator/statistics", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/cabinet/superadmin/activity"


def test_student_cannot_open_curator_statistics(auth_client):
    client, _ = auth_client
    assert client.get("/cabinet/curator/statistics", follow_redirects=False).status_code == 403


@pytest.mark.skipif(shutil.which("node") is None, reason="нужен node")
def test_students_by_name_script_parses(admin_client, db, user_factory, tmp_path):
    """Скрипт карточки рендерится Jinja (данные вшиты `tojson`) — проверяем
    готовую страницу: `node --check` по скрипту карточки."""
    client, _ = admin_client
    user_factory(vk_id=820_050, name="Student", tariff=TARIFF_SELF)
    _cycle_with_video(db)

    html = client.get("/cabinet/superadmin/activity").text
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    card = [s for s in scripts if "data-activity-portfolio" in s]
    assert len(card) == 1
    path = tmp_path / "card.js"
    path.write_text(card[0], encoding="utf-8")
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# ── Ответы Александрии 23.10.2026 ───────────────────────────────────────────

def test_theory_shows_minutes_for_unfinished_video(db, user_factory):
    """«Посмотрел 27 минут из 40» — у начавшего, но не досмотревшего."""
    from app.models.video_progress import VideoProgress

    started = user_factory(vk_id=820_060, name="Started", tariff=TARIFF_SELF)
    untouched = user_factory(vk_id=820_061, name="Untouched", tariff=TARIFF_SELF)
    _stage, _cycle, block, _upload = _cycle_with_video(db)
    video = db.get(LearningVideo, block.video_id)
    video.duration_seconds = 40 * 60
    db.add(VideoProgress(user_id=started.id, video_id=video.bunny_video_id,
                         covered_seconds=27 * 60 + 30))
    db.commit()

    item = get_student_activity_overview(db, include_assignments=True)["videos"][0]

    assert item["notes"][started.id] == "Смотрел 27 из 40 мин"
    assert item["notes"][untouched.id] == "Не посмотрел"
    assert item["started"] == [started.id]
    assert set(item["pending"]) == {started.id, untouched.id}


def test_cycle_label_gets_ordinal_unless_already_numbered(db, user_factory):
    """«5. Контрольная работа» — номер по порядку в этапе; «Цикл 1» и
    «1 неделя» не получают второй номер."""
    user_factory(vk_id=820_070, name="Student", tariff=TARIFF_SELF)
    stage, first, _block, _upload = _cycle_with_video(db, stage_title="Предобучение")
    first.title = "1 неделя"
    second = LearningTopic(title="Контрольная работа", kind="week", parent_id=stage.id,
                           opens_at=OPENS.replace(day=8), is_published=True, assign_to_all=True)
    db.add(second)
    db.flush()
    task = TrackerTask(title="Контрольная", topic_id=second.id, kind="material", is_published=True)
    db.add(task)
    db.flush()
    db.add(TaskBlock(task_id=task.id, block_type="photo_upload", title="Работа"))
    db.commit()

    months = get_student_activity_overview(db, include_assignments=True)["months"]

    assert [c["label"] for c in months[0]["cycles"]] == ["1 неделя", "2. Контрольная работа"]


def test_holiday_mode_filters_by_track_and_hides_foreign_tasks(db, user_factory):
    """Каникулярный курс: фильтр по треку вместо тарифа, задания годового
    курса, которые каникулярным не положены, в выбор не попадают."""
    from app.constants import PROGRAM_HOLIDAY
    from app.models.intake_link import IntakeLink
    from app.models.tag import Tag, UserTag
    from app.services.report_scope import report_program

    kid = user_factory(vk_id=820_080, name="Kid", tariff="")
    kid.program = PROGRAM_HOLIDAY
    tag = Tag(name="АРХИпроба 1 трек")
    db.add(tag)
    db.flush()
    db.add(IntakeLink(slug="arhi-1", title="АРХИпроба 1 трек", program=PROGRAM_HOLIDAY,
                      tag_id=tag.id, is_active=True))
    db.add(UserTag(user_id=kid.id, tag_id=tag.id))
    db.commit()
    stage, _cycle, _block, _upload = _cycle_with_video(db)
    stage.assign_to_all = False
    db.commit()

    with report_program(PROGRAM_HOLIDAY):
        overview = get_student_activity_overview(db, include_assignments=True)

    assert [row["tariff"] for row in overview["students"]] == ["АРХИпроба 1 трек"]
    assert overview["tariffs"] == ["АРХИпроба 1 трек"]
    assert overview["group_label"] == "Трек" and overview["show_level"] is False
    assert all(item["eligible"] for item in overview["videos"] + overview["assignments"])
