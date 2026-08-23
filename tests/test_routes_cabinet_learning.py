"""Tests for GET /cabinet/learning — «Актуальное образовательное пространство» (трек A)."""
from datetime import date, timedelta

from app.models.exam_cycle import ExamCycle
from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.services.program import day_bounds, week_start
from app.services.tracker import create_task
from app.services.tz import msk_midnight, now_msk, today_msk


def _cycle(db, user, *, subject="Рисунок", closed=False):
    cycle = ExamCycle(
        user_id=user.id, subject=subject, started_at=date.today(),
        closed_at=now_msk() if closed else None,
    )
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


def _topic(db, owner, *, assign_to_all=True, opens_in_days=-1, is_published=True,
           title="Неделя 1", meeting_url=None):
    topic = LearningTopic(
        title=title,
        opens_at=now_msk() + timedelta(days=opens_in_days),
        assign_to_all=assign_to_all,
        is_published=is_published,
        created_by_id=owner.id,
        meeting_url=meeting_url,
    )
    db.add(topic)
    db.commit()
    return topic


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


def test_learning_shows_empty_state_without_accessible_topics(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Пока нет ни одной доступной вам недели" in resp.text


def test_learning_shows_current_topic_title(auth_client, db):
    client, user = auth_client
    _topic(db, user, title="Неделя про композицию")

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Неделя про композицию" in resp.text


def test_learning_meeting_url_renders_join_link(auth_client, db):
    client, user = auth_client
    _topic(db, user, meeting_url="https://meet.example.com/week1")

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert 'href="https://meet.example.com/week1"' in resp.text
    assert "Присоединиться" in resp.text


def test_learning_without_meeting_url_shows_placeholder(auth_client, db):
    client, user = auth_client
    _topic(db, user, meeting_url=None)

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Ссылка на созвон появится позже" in resp.text


def test_learning_bottom_nav_highlights_learning_tab(auth_client, db):
    client, user = auth_client
    _topic(db, user)

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert 'href="/cabinet/learning"' in resp.text
    assert 'class="bottom-nav"' in resp.text


# ── Вкладки недели вместо календаря по датам (22.08/23.08) ──────────────────

def test_learning_shows_current_week_task(auth_client, db):
    client, user = auth_client
    day = week_start(today_msk()) + timedelta(days=2)
    due = day_bounds(day)[0] + timedelta(hours=10)
    task = create_task(
        db, title="Сдать эскиз", user_id=user.id, due_at=due,
        assign_to_all=True, kind="homework",
    )
    task.is_published = True
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Сдать эскиз" in resp.text
    assert 'class="lrn-tabs nav-pill"' in resp.text
    assert 'data-tab="homework"' in resp.text


def test_learning_shows_eight_tabs_in_fixed_order(auth_client, db):
    client, _ = auth_client

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    order = [
        "Материалы", "Видео", "Тест по теории", "Занятие",
        "Задание", "Чек-лист и проверки", "Анкета", "Обратная связь",
    ]
    # Ищем именно подписи кнопок вкладок, а не любое вхождение слова —
    # «Задание»/«Анкета»/«Занятие» встречаются ещё и во вступительном тексте
    # шапки («Задание, анкета и занятие текущей недели — в одном месте»).
    positions = [resp.text.index(f">{label}</button>") for label in order]
    assert positions == sorted(positions)


def test_learning_unfinished_task_locks_next_tab(auth_client, db):
    client, user = auth_client
    day = week_start(today_msk()) + timedelta(days=1)
    due = day_bounds(day)[0] + timedelta(hours=10)
    task = create_task(
        db, title="Домашка недели", user_id=user.id, due_at=due,
        assign_to_all=True, kind="homework",
    )
    task.is_published = True
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    # «Задание» (homework) не сдано — следующая по порядку вкладка «Чек-лист
    # и проверки» заперта с указанием причины.
    assert "Сначала сделай «Задание»." in resp.text


def test_learning_empty_tab_does_not_lock_next(auth_client, db):
    """На неделе нет ни материалов, ни видео, ни теста — эти вкладки пустые,
    но не запирают «Занятие»: блокировать нечем (решение владельца 23.08).
    Занятие ещё не сдано — оно само откроется, а уже дальше по цепочке
    («Задание» и т.д.) закономерно запрётся, это не проверяем здесь."""
    client, user = auth_client
    day = week_start(today_msk()) + timedelta(days=1)
    due = day_bounds(day)[0] + timedelta(hours=10)
    task = create_task(
        db, title="Эфир недели", user_id=user.id, due_at=due,
        assign_to_all=True, kind="lesson",
    )
    task.is_published = True
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Эфир недели" in resp.text
    # Пустые «Материалы»/«Видео»/«Тест по теории» не запирают «Занятие» —
    # его контент виден, а не спрятан за карточкой «Сначала сделай …».
    lesson_panel_start = resp.text.index('data-tabpanel="lesson"')
    lesson_panel_end = resp.text.index('data-tabpanel="homework"')
    lesson_panel = resp.text[lesson_panel_start:lesson_panel_end]
    assert "Эфир недели" in lesson_panel
    assert "Сначала сделай" not in lesson_panel


def test_learning_task_outside_current_week_not_shown(auth_client, db):
    client, user = auth_client
    day = week_start(today_msk()) + timedelta(days=14)
    due = day_bounds(day)[0] + timedelta(hours=10)
    task = create_task(db, title="Задача через две недели", user_id=user.id, due_at=due, assign_to_all=True)
    task.is_published = True
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Задача через две недели" not in resp.text


# ── Выбор актуальной недели (21.08) ─────────────────────────────────────────

def test_learning_shows_latest_opened_week_not_the_first(auth_client, db):
    """Открыты две недели — в шапке должна быть поздняя.

    Раньше выборка шла по возрастанию `opens_at`, а у `accessible_topic_ids`
    нет верхней границы окна: экран навсегда застревал на первой неделе курса
    и вместе с ней отдавал её ссылку на созвон.
    """
    client, user = auth_client
    _topic(db, user, title="Неделя 1", opens_in_days=-14,
           meeting_url="https://meet.example.com/week1")
    _topic(db, user, title="Неделя 3", opens_in_days=-1,
           meeting_url="https://meet.example.com/week3")

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Неделя 3" in resp.text
    assert "Неделя 1" not in resp.text
    assert 'href="https://meet.example.com/week3"' in resp.text
    assert "week1" not in resp.text


def test_learning_ignores_program_item_topics(auth_client, db):
    """Служебная тема элемента программы не может стать «актуальной неделей».

    `program.py::ensure_item_topic` заводит по теме на каждый элемент, и они
    открываются позже недели. Без фильтра по `kind` в шапку попадало название
    элемента, а `meeting_url` у служебной темы всегда пустой — ссылка на
    созвон не показывалась никогда.
    """
    from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM

    client, user = auth_client
    # Служебная тема открыта раньше недели — так бывает, когда неделю завели
    # позже её элементов. Дата вне значения (после фильтра по kind она не
    # участвует в подборе недели вовсе), важно только что она раньше.
    item = _topic(db, user, title="Видео недели", opens_in_days=-14)
    # effective_week_start без долгов у ученика возвращает понедельник текущей
    # календарной недели (гейт, решение владельца 23.08) — тему целим точно
    # туда, а не «семь дней назад», чтобы тест не зависел от дня недели запуска.
    monday_offset = -(today_msk() - week_start(today_msk())).days
    _topic(db, user, title="Неделя 2", opens_in_days=monday_offset,
           meeting_url="https://meet.example.com/week2")
    item.kind = TOPIC_KIND_PROGRAM_ITEM
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Неделя 2" in resp.text
    assert "Видео недели" not in resp.text
    assert 'href="https://meet.example.com/week2"' in resp.text


# ── Вкладка «Обратная связь» переиспользует ExamCycle/Feedback (23.08) ──────

# ── Гейт «блок → неделя → месяц» (23.08) ────────────────────────────────────

def test_learning_shows_debt_week_not_the_current_one(auth_client, db):
    """Ключевой регресс-тест: должник видит свою застрявшую неделю, не текущую.

    До гейта (решение владельца 23.08) экран всегда показывал календарную
    неделю независимо от долгов. После — ученик с незакрытым обязательным
    элементом прошлой недели остаётся на ней, пока не закроет долг.
    """
    client, user = auth_client
    today = today_msk()
    monday = week_start(today)
    last_monday = monday - timedelta(days=7)
    user.created_at = msk_midnight(last_monday - timedelta(days=30))
    db.commit()

    db.add(LearningTopic(
        title="Неделя с долгом", opens_at=msk_midnight(last_monday),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=user.id,
    ))
    db.add(LearningTopic(
        title="Текущая неделя", opens_at=msk_midnight(monday),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=user.id,
    ))
    db.commit()

    due = day_bounds(last_monday)[0] + timedelta(hours=10)
    task = create_task(
        db, title="Долг прошлой недели", user_id=user.id, due_at=due,
        assign_to_all=True, kind="homework",
    )
    task.is_published = True
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Неделя с долгом" in resp.text
    assert "Долг прошлой недели" in resp.text
    assert "Текущая неделя" not in resp.text


def test_learning_shows_current_week_when_no_debt(auth_client, db):
    """Без долгов ученик по-прежнему видит текущую календарную неделю."""
    client, user = auth_client
    today = today_msk()
    monday = week_start(today)

    db.add(LearningTopic(
        title="Текущая неделя", opens_at=msk_midnight(monday),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=user.id,
    ))
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Текущая неделя" in resp.text


def test_learning_feedback_tab_shows_placeholder_without_open_cycle(auth_client):
    client, _ = auth_client

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Открытых циклов Пробника нет" in resp.text


def test_learning_feedback_tab_links_to_open_cycle_dialog(auth_client, db):
    client, user = auth_client
    cycle = _cycle(db, user, subject="Композиция")

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert f'href="/cabinet/feedback/{cycle.id}"' in resp.text
    assert 'data-subject="Композиция"' in resp.text
    assert "Открытых циклов Пробника нет" not in resp.text


def test_learning_feedback_tab_hides_closed_cycles(auth_client, db):
    """Закрытый цикл не требует действия прямо сейчас — вкладка АОП его не
    показывает, полная история доступна на /cabinet/cycle."""
    client, user = auth_client
    closed = _cycle(db, user, closed=True)

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert f'href="/cabinet/feedback/{closed.id}"' not in resp.text
    assert "Открытых циклов Пробника нет" in resp.text


def test_learning_marks_task_subject_for_the_switch(auth_client, db):
    """Задачи выводятся с `data-subject` — по нему переключатель их и фильтрует.

    Сама фильтрация живёт в JS, здесь держим контракт разметки: без атрибута
    переключатель снова станет декоративным, как до 21.08.
    """
    client, user = auth_client
    _topic(db, user, title="Неделя 1")
    day = week_start(today_msk()) + timedelta(days=1)
    due = day_bounds(day)[0] + timedelta(hours=10)
    task = create_task(
        db, title="Натюрморт", user_id=user.id, due_at=due,
        assign_to_all=True, subject="Рисунок",
    )
    task.is_published = True
    db.commit()

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert 'data-subject="Рисунок"' in resp.text
    # Переключатель обязан стоять выше списка, который фильтрует.
    assert resp.text.index('class="lrn-subject-toggle"') < resp.text.index('class="lrn-tabs nav-pill"')
