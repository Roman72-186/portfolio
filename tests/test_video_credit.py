"""«Засчитать просмотр» в карточке ученика — ГП и суперадмин (владелец
10.10.2026, ученица id 288: плеер три дня не передавал просмотр, кружок
отказал 39 раз, засчитать было нечем, кроме скрипта на сервере).

Правило — `services/video_credit.py`: список видео-блоков ученика с циклом,
зачёт закрывает блок тем же `close_block_for_user`, что и кружок, с
сотрудником в `completed_by_id`; `VideoProgress` не трогается.
"""
import pytest

from app.models.learning_video import LearningVideo
from app.models.task_block import BLOCK_TEXT, BLOCK_VIDEO, TaskBlock, TaskBlockState
from app.models.tracker import TrackerTaskState
from app.models.video_progress import VideoProgress


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


@pytest.fixture()
def people(user_factory):
    curator = user_factory(vk_id=961_001, name="Куратор", role_name="куратор")
    chief = user_factory(vk_id=961_002, name="Главный", is_admin=True, role_name="админ")
    superadmin = user_factory(vk_id=961_003, name="Супер", is_admin=True, role_name="суперадмин")
    student = user_factory(vk_id=961_004, name="Ученик", role_name="ученик")
    student.curator_id = curator.id
    return {"curator": curator, "chief": chief, "superadmin": superadmin, "student": student}


def _video_task(db, staff, *, title="Старт", video_title="Устройство месяца", published=True, extra_text=False):
    from app.services.tracker import create_task

    video = LearningVideo(
        bunny_library_id=1, bunny_video_id=f"v-{title}", title=video_title,
        status="ready", is_published=True, duration_seconds=585.0,
    )
    db.add(video)
    task = create_task(db, title=title, user_id=staff.id, kind="material", assign_to_all=True)
    task.is_published = published
    db.flush()
    block = TaskBlock(task_id=task.id, sort_order=0, block_type=BLOCK_VIDEO, video_id=video.id, is_required=True)
    db.add(block)
    if extra_text:
        db.add(TaskBlock(task_id=task.id, sort_order=1, block_type=BLOCK_TEXT, body="Прочитай"))
    db.commit()
    return task, block, video


def _credit(client, student, block):
    return client.post(f"/cabinet/students/{student.id}/video-blocks/{block.id}/credit")


@pytest.mark.parametrize("role", ["chief", "superadmin"])
def test_chief_and_superadmin_credit_video(client, db, session_factory, people, role):
    staff = people[role]
    task, block, video = _video_task(db, staff)
    _login(client, session_factory, staff)

    listed = client.get(f"/cabinet/students/{people['student'].id}/statistics").json()["video_credit"]
    assert listed == [{
        "block_id": block.id, "video": "Устройство месяца", "task": "Старт",
        "place": "Без цикла", "done": False,
    }]

    resp = _credit(client, people["student"], block)

    assert resp.status_code == 200
    assert resp.json()["video_credit"][0]["done"] is True
    state = db.query(TaskBlockState).filter_by(block_id=block.id, user_id=people["student"].id).one()
    assert (state.status, state.completion_source, state.completed_by_id) == ("done", "video_staff", staff.id)
    # Видео было единственным шагом — задание закрылось само, как от кружка.
    assert db.query(TrackerTaskState).filter_by(task_id=task.id, user_id=people["student"].id).one().status == "done"
    # Статистика просмотра честная: засчитан, но не просмотрен.
    assert db.query(VideoProgress).count() == 0


def test_curator_cannot_credit_and_does_not_see_list(client, db, session_factory, people):
    _, block, _ = _video_task(db, people["chief"])
    _login(client, session_factory, people["curator"])

    assert client.get(f"/cabinet/students/{people['student'].id}/statistics").json()["video_credit"] is None
    assert _credit(client, people["student"], block).status_code == 403
    assert db.query(TaskBlockState).count() == 0


def test_block_the_student_does_not_have_is_404(client, db, session_factory, people):
    """Засчитать можно только то, что ученику поставлено: неопубликованное
    задание в его ленте не существует."""
    _, block, _ = _video_task(db, people["chief"], published=False)
    _login(client, session_factory, people["chief"])

    assert client.get(f"/cabinet/students/{people['student'].id}/statistics").json()["video_credit"] == []
    assert _credit(client, people["student"], block).status_code == 404
    assert db.query(TaskBlockState).count() == 0


def test_repeat_credit_keeps_first_closer(client, db, session_factory, people):
    """Уже закрытый блок не перезаписывается: ни время, ни кто закрыл."""
    _, block, _ = _video_task(db, people["chief"], extra_text=True)
    _login(client, session_factory, people["chief"])
    assert _credit(client, people["student"], block).status_code == 200
    _login(client, session_factory, people["superadmin"])

    assert _credit(client, people["student"], block).status_code == 200

    state = db.query(TaskBlockState).filter_by(block_id=block.id, user_id=people["student"].id).one()
    assert state.completed_by_id == people["chief"].id


def test_list_names_cycle_and_stage(db, people):
    """В списке — к какому циклу относится ролик (владелец 10.10.2026)."""
    from app.models.learning_topic import LearningTopic
    from app.services.video_credit import _place

    assert _place(LearningTopic(title="Неделя 1", kind="week")) == "Цикл «Неделя 1»"
    assert _place(LearningTopic(title="Портфолио", kind="stage")) == "Этап «Портфолио»"
    assert _place(None) == "Без цикла"
