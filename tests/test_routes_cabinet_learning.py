"""Tests for GET /cabinet/learning — единая лента заданий цикла.

С 06.09.2026 экран рисует ленту, а не восемь вкладок недели (решение
владельца, голосовое 01:37: «мне смысл эти восемь кнопок держать?»). Тесты
вкладок — фиксированный порядок восьмёрки, блокировка следующей вкладки,
маркеры на кнопках, вкладка «Обратная связь» — сняты вместе с самими
вкладками; то, что они охраняли по смыслу (блокировка последовательности,
видимость задач периода, инлайн-видео), проверяется здесь на ленте.

План — plans/2026-09-06-apparchi-block-feed-replaces-week-tabs.md, этап 2.
"""
import pathlib
from datetime import timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, TOPIC_KIND_WEEK, LearningTopic
from app.models.learning_video import LearningVideo
from app.models.task_block import BLOCK_TEXT, BLOCK_VIDEO, TaskBlock
from app.services.program import day_bounds, week_start
from app.services.task_blocks import close_block_for_user
from app.services.tracker import create_task
from app.services.tz import now_msk, today_msk

TRACKER_CSS = (
    pathlib.Path(__file__).parent.parent / "app" / "static" / "css" / "tracker.css"
).read_text(encoding="utf-8")


def _topic(db, owner, *, assign_to_all=True, opens_in_days=-1, is_published=True,
           title="Неделя 1", meeting_url=None, kind=TOPIC_KIND_WEEK, ends_in_days=None):
    topic = LearningTopic(
        title=title,
        opens_at=now_msk() + timedelta(days=opens_in_days),
        ends_at=None if ends_in_days is None else now_msk() + timedelta(days=ends_in_days),
        assign_to_all=assign_to_all,
        is_published=is_published,
        created_by_id=owner.id,
        meeting_url=meeting_url,
        kind=kind,
    )
    db.add(topic)
    db.commit()
    return topic


def _task(db, user, *, title, kind="homework", day=None, is_required=True, order=0):
    day = day or (week_start(today_msk()) + timedelta(days=2))
    task = create_task(
        db, title=title, user_id=user.id,
        due_at=day_bounds(day)[0] + timedelta(hours=10),
        assign_to_all=True, kind=kind, is_required=is_required,
    )
    task.is_published = True
    task.sort_order = order
    db.commit()
    db.refresh(task)
    return task


def _block(db, task, *, title, order=0, is_required=True):
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_TEXT, title=title, body="текст",
        sort_order=order, is_required=is_required,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


# ── доступ ──────────────────────────────────────────────────────────────────

def test_learning_without_auth_redirects(client):
    resp = client.get("/cabinet/learning", follow_redirects=False)
    assert resp.status_code == 302
    assert "session_expired" in resp.headers["location"]


def test_learning_redirects_to_profile_when_incomplete(client, user_factory, session_factory):
    user = user_factory(profile_completed=False)
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get("/cabinet/learning", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/profile"


# ── каркас экрана ───────────────────────────────────────────────────────────

def test_learning_never_shows_the_old_no_week_banner(auth_client):
    """Решение владельца 25.08.2026: баннер вводил в заблуждение, когда задачи
    у ученика есть, а рамка вокруг них не заведена. Лента, как и вкладки до
    неё, строится без цикла — на дефолтном заголовке."""
    client, _ = auth_client
    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Пока нет ни одной доступной вам недели" not in resp.text
    assert "Актуальное образовательное пространство" in resp.text


def test_learning_has_no_week_tabs_anymore(auth_client, db):
    """Восемь вкладок сняты решением владельца 06.09.2026 — «никаких вкладок,
    только конструктор с фильтрами»."""
    client, user = auth_client
    _task(db, user, title="Задание")

    resp = client.get("/cabinet/learning")
    assert 'class="lrn-tabs nav-pill"' not in resp.text
    assert 'data-tab="homework"' not in resp.text


def test_learning_shows_cycle_title(auth_client, db):
    """Период задан явно: без `ends_at` тема считается прежней неделей
    (понедельник плюс шесть дней), и по понедельникам «открытая вчера» тема
    попадала в прошлую неделю — заголовка не было, тест падал от дня запуска.
    """
    client, user = auth_client
    _topic(db, user, title="Цикл про композицию", ends_in_days=5)

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Цикл про композицию" in resp.text


def test_learning_bottom_nav_highlights_learning_tab(auth_client, db):
    client, user = auth_client
    _topic(db, user)

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert 'href="/cabinet/learning"' in resp.text
    assert 'class="bottom-nav"' in resp.text


# ── лента ───────────────────────────────────────────────────────────────────

def test_learning_shows_task_of_the_current_period(auth_client, db):
    client, user = auth_client
    _task(db, user, title="Сдать эскиз")

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Сдать эскиз" in resp.text
    assert 'class="lrn-feed"' in resp.text


def test_learning_task_outside_the_period_not_shown(auth_client, db):
    client, user = auth_client
    _task(db, user, title="Через месяц", day=today_msk() + timedelta(days=40))

    resp = client.get("/cabinet/learning")
    assert "Через месяц" not in resp.text


def test_learning_blocks_follow_the_teacher_order(auth_client, db):
    client, user = auth_client
    task = _task(db, user, title="Задание")
    _block(db, task, title="Второй шаг", order=2)
    _block(db, task, title="Первый шаг", order=1)

    resp = client.get("/cabinet/learning")
    assert resp.text.index("Первый шаг") < resp.text.index("Второй шаг")


def test_learning_locks_the_step_after_an_unfinished_one(auth_client, db):
    """То, что раньше держала блокировка вкладок: следующий шаг закрыт, пока
    не сделан предыдущий."""
    client, user = auth_client
    task = _task(db, user, title="Задание")
    _block(db, task, title="Первый шаг", order=1)
    _block(db, task, title="Второй шаг", order=2)

    resp = client.get("/cabinet/learning")
    assert "lrn-step--current" in resp.text
    assert "lrn-step--locked" in resp.text
    assert "Откроется, когда будет сделано предыдущее" in resp.text


def test_learning_done_step_opens_the_next(auth_client, db):
    client, user = auth_client
    task = _task(db, user, title="Задание")
    first = _block(db, task, title="Первый шаг", order=1)
    _block(db, task, title="Второй шаг", order=2)
    close_block_for_user(db, block=first, user_id=user.id, source="manual")
    db.commit()

    resp = client.get("/cabinet/learning")
    assert "lrn-step--done" in resp.text
    assert "lrn-step--locked" not in resp.text


def test_learning_shows_progress_counter(auth_client, db):
    client, user = auth_client
    task = _task(db, user, title="Задание")
    first = _block(db, task, title="Первый шаг", order=1)
    _block(db, task, title="Второй шаг", order=2)
    close_block_for_user(db, block=first, user_id=user.id, source="manual")
    db.commit()

    resp = client.get("/cabinet/learning")
    assert "Сделано 1 из 2" in resp.text


def test_learning_closed_block_shows_curator_message(auth_client, db):
    """«27 сентября в 23:30 закрывается доступ» с текстом куратора вместо
    стандартной фразы — свой текст должен перекрыть шаблонную «Доступ закрыт»."""
    client, user = auth_client
    task = _task(db, user, title="Задание")
    block = _block(db, task, title="Модуль", order=1, is_required=False)
    block.closes_at = now_msk().astimezone(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    block.locked_message = "Пока проверь чат-комьюнити в телеграмме."
    db.commit()

    resp = client.get("/cabinet/learning")
    assert "lrn-step--locked" in resp.text
    assert "Пока проверь чат-комьюнити в телеграмме." in resp.text


def test_learning_closed_block_without_message_shows_default(auth_client, db):
    client, user = auth_client
    task = _task(db, user, title="Задание")
    block = _block(db, task, title="Модуль", order=1, is_required=False)
    block.closes_at = now_msk().astimezone(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.commit()

    resp = client.get("/cabinet/learning")
    assert "Доступ закрыт." in resp.text


def test_learning_optional_block_does_not_lock_the_tail(auth_client, db):
    client, user = auth_client
    task = _task(db, user, title="Задание")
    _block(db, task, title="Необязательный", order=1, is_required=False)
    _block(db, task, title="Следующий", order=2)

    resp = client.get("/cabinet/learning")
    assert "lrn-step--locked" not in resp.text


# ── задачи без блоков ───────────────────────────────────────────────────────

def test_learning_video_task_expands_inline_instead_of_linking_away(auth_client, db):
    """Инлайн-показ ролика (владелец 29.08) в ленте сохраняется: карточка
    задачи без блоков раскрывается на месте, а не уводит на отдельный экран."""
    client, user = auth_client
    video = LearningVideo(
        bunny_library_id=1, bunny_video_id="vid-1", title="Урок",
        is_published=True, status="ready",
    )
    db.add(video)
    db.flush()
    task = _task(db, user, title="Посмотреть урок", kind="video")
    task.source_kind = "learning_video"
    task.source_id = video.id
    db.commit()

    resp = client.get("/cabinet/learning")
    assert "Посмотреть урок" in resp.text
    assert 'href="/cabinet/videos/' not in resp.text


def test_learning_video_step_shows_completion_circle(auth_client, db):
    """Кружок в углу карточки (владелец 12.09.2026) — только у видео: и у
    старой видео-задачи целиком (`kind='video'`, без блоков), и у видео-блока
    конструктора. У остальных типов его нет — им хватает текстового бейджа
    «Сделано»."""
    client, user = auth_client
    video = LearningVideo(
        bunny_library_id=1, bunny_video_id="vid-circ", title="Урок",
        is_published=True, status="ready",
    )
    db.add(video)
    db.flush()

    legacy_video_task = _task(db, user, title="Видео-задача", kind="video", order=0)
    legacy_video_task.source_kind = "learning_video"
    legacy_video_task.source_id = video.id

    text_task = _task(db, user, title="Текстовое задание", kind="material", order=1)
    _block(db, text_task, title="Текст", order=0)

    block_video_task = _task(db, user, title="Задание с видео-блоком", kind="material", order=2)
    db.add(TaskBlock(
        task_id=block_video_task.id, block_type=BLOCK_VIDEO, video_id=video.id,
        sort_order=0, is_required=False,
    ))
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.text.count("lrn-step-check") == 2


def test_learning_task_without_blocks_locks_the_tail(auth_client, db):
    client, user = auth_client
    _task(db, user, title="Видео", kind="video",
          day=week_start(today_msk()) + timedelta(days=1))
    later = _task(db, user, title="Материал",
                  day=week_start(today_msk()) + timedelta(days=3))
    _block(db, later, title="Шаг материала", order=1)

    resp = client.get("/cabinet/learning")
    assert "lrn-step--locked" in resp.text


def test_learning_mock_exam_does_not_lock_the_tail(auth_client, db):
    """Билет Пробника блокирует месяц, а не цикл (решение владельца 23.08)."""
    client, user = auth_client
    _task(db, user, title="Пробник", kind="mock_exam",
          day=week_start(today_msk()) + timedelta(days=1))
    later = _task(db, user, title="Материал",
                  day=week_start(today_msk()) + timedelta(days=3))
    _block(db, later, title="Шаг материала", order=1)

    resp = client.get("/cabinet/learning")
    assert "lrn-step--locked" not in resp.text


# ── адресация и предмет ─────────────────────────────────────────────────────

def test_learning_ignores_program_item_topics(auth_client, db):
    """Служебная тема элемента программы циклом не считается — иначе каждый
    элемент дня стал бы собственным циклом."""
    client, user = auth_client
    _topic(db, user, title="Служебная тема", kind=TOPIC_KIND_PROGRAM_ITEM)

    resp = client.get("/cabinet/learning")
    assert "Служебная тема" not in resp.text


def test_learning_marks_task_subject_for_the_switch(auth_client, db):
    client, user = auth_client
    task = _task(db, user, title="Работа по рисунку")
    task.subject = "Рисунок"
    db.commit()

    resp = client.get("/cabinet/learning")
    assert 'data-subject="Рисунок"' in resp.text
    assert 'class="lrn-subject-toggle"' in resp.text


def test_learning_hides_the_switch_without_subjects(auth_client, db):
    """Владелец 03.09.2026: «в предыдущих циклах эти кнопки не нужны — мы
    просто не будем ставить разделение, и кнопок в принципе не будет».
    В предобучении до 29 сентября деления на предметы нет вовсе."""
    client, user = auth_client
    _task(db, user, title="Общее задание")

    resp = client.get("/cabinet/learning")
    assert 'class="lrn-subject-toggle"' not in resp.text


def test_learning_switch_lists_only_present_subjects(auth_client, db):
    client, user = auth_client
    task = _task(db, user, title="Только рисунок")
    task.subject = "Рисунок"
    db.commit()

    resp = client.get("/cabinet/learning")
    assert 'data-subject="Рисунок"' in resp.text
    assert 'data-subject="Композиция"' not in resp.text


def test_learning_cycle_period_widens_the_window(auth_client, db):
    """Трёхнедельный цикл показывает задание, которое в календарную неделю не
    попадает, — ради этого период и заводился."""
    client, user = auth_client
    _topic(db, user, title="Цикл предобучения", opens_in_days=-3, ends_in_days=11)
    _task(db, user, title="Через десять дней", day=today_msk() + timedelta(days=10))

    resp = client.get("/cabinet/learning")
    assert "Через десять дней" in resp.text


# ── статика ─────────────────────────────────────────────────────────────────

def test_trk_row_and_lrn_step_respect_the_hidden_attribute():
    """Фильтр предмета прячет шаги атрибутом `hidden` — CSS c `display: flex`
    его перебивал бы, и «скрытая» карточка осталась бы на экране."""
    assert ".lrn-step[hidden] { display: none; }" in TRACKER_CSS


def test_tracker_css_has_no_stray_jinja_comment_terminator():
    """`#}` внутри CSS означает, что комментарий шаблона уехал в статику."""
    assert "#}" not in TRACKER_CSS


# ── возврат в пройденные циклы (владелец 03.09.2026) ────────────────────────

def test_learning_lists_past_cycles(auth_client, db):
    """«Он может вернуться в этот цикл, потому что у каждого цикла своя тема»."""
    client, user = auth_client
    _topic(db, user, title="Первый цикл", opens_in_days=-20, ends_in_days=-10)
    _topic(db, user, title="Второй цикл", opens_in_days=-3, ends_in_days=10)

    resp = client.get("/cabinet/learning")

    assert "Первый цикл" in resp.text
    assert "Второй цикл" in resp.text
    assert 'class="lrn-cycles"' in resp.text


def test_learning_opens_a_past_cycle_read_only(auth_client, db):
    client, user = auth_client
    past = _topic(db, user, title="Первый цикл", opens_in_days=-20, ends_in_days=-10)
    _topic(db, user, title="Второй цикл", opens_in_days=-3, ends_in_days=10)

    resp = client.get(f"/cabinet/learning?cycle={past.id}")

    assert resp.status_code == 200
    assert "Пройденный цикл" in resp.text


def test_learning_ignores_a_future_cycle_in_the_link(auth_client, db):
    """Не начавшийся цикл вперёд не выдаётся, даже если номер подобрали руками."""
    client, user = auth_client
    future = _topic(db, user, title="Будущий цикл", opens_in_days=5, ends_in_days=15)

    resp = client.get(f"/cabinet/learning?cycle={future.id}")

    assert resp.status_code == 200
    assert "Пройденный цикл" not in resp.text


def test_learning_hides_cycle_chips_when_there_is_one_cycle(auth_client, db):
    client, user = auth_client
    _topic(db, user, title="Единственный", opens_in_days=-3, ends_in_days=10)

    resp = client.get("/cabinet/learning")

    assert 'class="lrn-cycles"' not in resp.text
