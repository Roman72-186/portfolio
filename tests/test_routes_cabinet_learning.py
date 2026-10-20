"""Tests for GET /cabinet/learning — единая лента заданий цикла.

С 06.09.2026 экран рисует ленту, а не восемь вкладок недели (решение
владельца, голосовое 01:37: «мне смысл эти восемь кнопок держать?»). Тесты
вкладок — фиксированный порядок восьмёрки, блокировка следующей вкладки,
маркеры на кнопках, вкладка «Обратная связь» — сняты вместе с самими
вкладками; то, что они охраняли по смыслу (блокировка последовательности,
видимость задач периода, инлайн-видео), проверяется здесь на ленте.

План — plans/2026-09-06-apparchi-block-feed-replaces-week-tabs.md, этап 2.
"""
from datetime import timedelta, timezone


from app.models.learning_topic import TOPIC_KIND_PROGRAM_ITEM, TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import BLOCK_TEXT, TaskBlock
from app.models.tracker import STATUS_DONE
from app.services.cycle_feed import (
    LOCK_BY_CLOSED,
    LOCK_BY_DATE,
    LOCK_BY_SEQUENCE,
    STATUS_CURRENT,
    STATUS_LOCKED,
)
from app.services.program import day_bounds, week_start
from app.services.task_blocks import close_block_for_user
from app.services.tracker import create_task
from app.services.tz import now_msk, today_msk

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


def _block(db, task, *, title, order=0, is_required=True, block_type=BLOCK_TEXT):
    block = TaskBlock(
        task_id=task.id, block_type=block_type, title=title, body="текст",
        sort_order=order, is_required=is_required,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


def _feed(client, rendered, url="/cabinet/learning"):
    """Данные ленты, которые роут отдал в шаблон."""
    assert client.get(url).status_code == 200
    return rendered("cabinet_learning.html")


def _steps(page):
    """Шаги ленты по подписи: блок — своим заголовком, задание без блоков — своим."""
    return {
        (step["block"].title if step["block"] else step["task"].title): step
        for step in page["feed"]["steps"]
    }


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


# ── лента ───────────────────────────────────────────────────────────────────

def test_learning_shows_task_of_the_current_period(auth_client, db):
    client, user = auth_client
    _task(db, user, title="Сдать эскиз")

    resp = client.get("/cabinet/learning")
    assert resp.status_code == 200
    assert "Сдать эскиз" in resp.text


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


def test_learning_locks_the_step_after_an_unfinished_one(auth_client, db, rendered):
    """То, что раньше держала блокировка вкладок: следующий шаг закрыт, пока
    не сделан предыдущий."""
    client, user = auth_client
    task = _task(db, user, title="Задание")
    # Держит очередь сдача работы, а не текст: текст и ссылка обязательными
    # не бывают (владелец 01.10.2026, `is_block_required_for_user`).
    _block(db, task, title="Первый шаг", order=1, block_type="upload")
    _block(db, task, title="Второй шаг", order=2)

    steps = _steps(_feed(client, rendered))
    assert steps["Первый шаг"]["status"] == STATUS_CURRENT
    assert steps["Второй шаг"]["status"] == STATUS_LOCKED
    assert steps["Второй шаг"]["lock_reason"] == LOCK_BY_SEQUENCE


def test_learning_tells_the_hour_a_step_opens(auth_client, db, rendered):
    """Шаг ждёт даты — и дата открытия сохраняет час, а не только день.

    До 27.09.2026 открытие всегда приходилось на полночь. Теперь
    преподаватель ставит час, и ученик должен видеть именно его.
    """
    client, user = auth_client
    task = _task(db, user, title="Задание")
    block = _block(db, task, title="Теория", order=1)
    opens_at = (now_msk() + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    block.opens_at = opens_at.astimezone(timezone.utc)
    db.commit()

    step = _steps(_feed(client, rendered))["Теория"]
    assert step["status"] == STATUS_LOCKED
    assert step["lock_reason"] == LOCK_BY_DATE
    shown = step["opens_on"]
    if shown.tzinfo is None:
        shown = shown.replace(tzinfo=timezone.utc)
    assert shown.astimezone(opens_at.tzinfo) == opens_at


def test_learning_done_step_opens_the_next(auth_client, db, rendered):
    client, user = auth_client
    task = _task(db, user, title="Задание")
    first = _block(db, task, title="Первый шаг", order=1)
    _block(db, task, title="Второй шаг", order=2)
    close_block_for_user(db, block=first, user_id=user.id, source="manual")
    db.commit()

    steps = _steps(_feed(client, rendered))
    assert steps["Первый шаг"]["status"] == STATUS_DONE
    assert steps["Второй шаг"]["status"] == STATUS_CURRENT


def test_learning_shows_progress_counter(auth_client, db, rendered):
    client, user = auth_client
    task = _task(db, user, title="Задание")
    first = _block(db, task, title="Первый шаг", order=1)
    _block(db, task, title="Второй шаг", order=2)
    close_block_for_user(db, block=first, user_id=user.id, source="manual")
    db.commit()

    feed = _feed(client, rendered)["feed"]
    assert (feed["done_count"], feed["total_count"]) == (1, 2)


def test_learning_optional_block_does_not_lock_the_tail(auth_client, db, rendered):
    client, user = auth_client
    task = _task(db, user, title="Задание")
    _block(db, task, title="Необязательный", order=1, is_required=False)
    _block(db, task, title="Следующий", order=2)

    steps = _steps(_feed(client, rendered))
    assert steps["Следующий"]["status"] == STATUS_CURRENT


# ── задачи без блоков ───────────────────────────────────────────────────────


def test_learning_video_task_closes_only_by_watch(auth_client, db):
    """Контроль просмотра включён снова 05.10.2026 (с 19.09 был выключен): у
    видео-задания без блоков нет кнопки «Завершить задание», его закрывает
    досмотр. С 20.10.2026 ручную отметку отклоняет и сервер — до этого прятал
    кнопку только экран, а запрос засчитывал ролик без просмотра."""
    from app.models.tracker import TrackerTaskState

    client, user = auth_client
    task = _task(db, user, title="Видео с контролем", kind="video")

    resp = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")

    assert resp.status_code == 403
    state = db.query(TrackerTaskState).filter_by(task_id=task.id, user_id=user.id).one_or_none()
    assert state is None or state.status != STATUS_DONE


def test_learning_video_task_with_blocks_keeps_its_completion_button(auth_client, db):
    """Видео, собранное из блоков, закрывается кнопкой «Завершить задание», как
    любое задание с блоками: запрет выше его не касается."""
    client, user = auth_client
    task = _task(db, user, title="Видео с текстом", kind="video", day=today_msk())
    task.due_at = day_bounds(today_msk())[1] - timedelta(minutes=1)
    _block(db, task, title="Перед просмотром", order=0, is_required=False)

    resp = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == STATUS_DONE



def test_learning_task_without_blocks_locks_the_tail(auth_client, db, rendered):
    client, user = auth_client
    _task(db, user, title="Видео", kind="video",
          day=week_start(today_msk()) + timedelta(days=1))
    later = _task(db, user, title="Материал",
                  day=week_start(today_msk()) + timedelta(days=3))
    _block(db, later, title="Шаг материала", order=1)

    steps = _steps(_feed(client, rendered))
    assert steps["Видео"]["status"] == STATUS_CURRENT
    assert steps["Шаг материала"]["lock_reason"] == LOCK_BY_SEQUENCE


def test_learning_mock_exam_does_not_lock_the_tail(auth_client, db, rendered):
    """Билет Пробника блокирует месяц, а не цикл (решение владельца 23.08)."""
    client, user = auth_client
    _task(db, user, title="Пробник", kind="mock_exam",
          day=week_start(today_msk()) + timedelta(days=1))
    later = _task(db, user, title="Материал",
                  day=week_start(today_msk()) + timedelta(days=3))
    _block(db, later, title="Шаг материала", order=1)

    steps = _steps(_feed(client, rendered))
    assert steps["Шаг материала"]["status"] == STATUS_CURRENT


# ── адресация и предмет ─────────────────────────────────────────────────────

def test_learning_ignores_program_item_topics(auth_client, db):
    """Служебная тема элемента программы циклом не считается — иначе каждый
    элемент дня стал бы собственным циклом."""
    client, user = auth_client
    _topic(db, user, title="Служебная тема", kind=TOPIC_KIND_PROGRAM_ITEM)

    resp = client.get("/cabinet/learning")
    assert "Служебная тема" not in resp.text


def test_completion_button_is_off_with_reason_until_questions_answered(auth_client, db, rendered):
    """Аудит АОП 30.09.2026, находка 2: до ответа на вопросы «Завершить задание»
    была активной, сервер отвечал 409, а ученик читал «Не получилось» и жал снова.
    Теперь кнопка выключена с причиной — по тому же правилу, что у сервера."""
    from app.models.task_block import BLOCK_QUESTION, BLOCK_UPLOAD, QUESTION_TEXT

    client, user = auth_client
    task = _task(db, user, title="Опрос", kind="material", day=today_msk())
    # Срок — конец сегодняшнего дня: с дефолтным «10:00» ответ после десяти
    # утра отклоняется как просроченный, и тест зависел бы от часа запуска.
    task.due_at = day_bounds(today_msk())[1] - timedelta(minutes=1)
    question = TaskBlock(
        task_id=task.id, block_type=BLOCK_QUESTION, question_type=QUESTION_TEXT,
        body="Как прошло?", sort_order=0, is_required=False,
    )
    # Второй невыполненный шаг — иначе ответ на единственный вопрос закрыл бы
    # задание сам («закрывается само», 30.09.2026).
    db.add_all([question, TaskBlock(
        task_id=task.id, block_type=BLOCK_UPLOAD, body="Сдай работу",
        sort_order=1, is_required=False,
    )])
    db.commit()

    reason = _feed(client, rendered)["completion_blockers"][task.id]
    assert reason
    # Экран и сервер отказывают по одному правилу.
    blocked = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == reason

    answered = client.post(
        f"/cabinet/tracker/tasks/{task.id}/blocks",
        json={"answers": [{"block_id": question.id, "text": "Хорошо"}]},
    )
    assert answered.status_code == 200, answered.text
    assert not _feed(client, rendered)["completion_blockers"].get(task.id)
    assert client.post(f"/cabinet/tracker/tasks/{task.id}/toggle").status_code == 200


def _task_with_upload(db, user, *, title, upload_required):
    """Задание: текст + «Домашнее задание». Отметить можно только сдачу работы."""
    from app.models.task_block import BLOCK_UPLOAD

    task = _task(db, user, title=title, kind="material", day=today_msk())
    task.due_at = day_bounds(today_msk())[1] - timedelta(minutes=1)
    # Текст — необязательный, как его заводит конструктор: обязательный текст
    # запер бы всё ниже, и до кнопки дело не дошло бы.
    _block(db, task, title="Как сдавать", order=0, is_required=False)
    upload = TaskBlock(
        task_id=task.id, block_type=BLOCK_UPLOAD, body="Сдай работу",
        sort_order=1, is_required=upload_required,
    )
    db.add(upload)
    db.commit()
    return task, upload


def test_completion_button_hidden_where_the_task_closes_itself(auth_client, db, rendered):
    """Владелец 01.10.2026 (аудит АОП, находка 8): «убираем лишнюю». Все шаги
    задания обязательные — оно закроется само, кнопки нет."""
    client, user = auth_client
    task, upload = _task_with_upload(db, user, title="Перспектива", upload_required=True)

    assert task.id in _feed(client, rendered)["completion_hidden"]
    close_block_for_user(db, upload, user.id, source="upload")
    db.commit()

    step = next(s for s in _feed(client, rendered)["feed"]["steps"] if s["task"].id == task.id)
    assert step["entry"]["status"] == STATUS_DONE


def test_completion_button_stays_for_an_optional_step(auth_client, db, rendered):
    """Необязательный шаг автозакрытие тоже ждёт: без кнопки ученику пришлось бы
    делать необязательное, чтобы закрыть задание."""
    client, user = auth_client
    task, _ = _task_with_upload(db, user, title="Перспектива", upload_required=False)

    page = _feed(client, rendered)
    assert task.id not in page["completion_hidden"]
    assert not page["completion_blockers"].get(task.id)
    assert client.post(f"/cabinet/tracker/tasks/{task.id}/toggle").status_code == 200


def test_completion_button_returns_when_steps_done_but_task_open(auth_client, db, rendered):
    """Страховка от тупика: шаги отмечены, а задание открыто (отметки до
    автозакрытия 30.09.2026) — кнопка возвращается."""
    from app.models.task_block import TaskBlockState

    client, user = auth_client
    task, upload = _task_with_upload(db, user, title="Перспектива", upload_required=True)
    # Прямой записью, мимо `close_block_for_user`: она закрыла бы задание сама.
    db.add(TaskBlockState(block_id=upload.id, user_id=user.id, status=STATUS_DONE))
    db.commit()

    page = _feed(client, rendered)
    assert task.id not in page["completion_hidden"]
    assert not page["completion_blockers"].get(task.id)
    assert client.post(f"/cabinet/tracker/tasks/{task.id}/toggle").status_code == 200


def _task_with_required_and_optional_step(db, user):
    """Необязательный ролик, за ним обязательная сдача работы: кнопка у задания
    есть (`completion_button_needed`) и стоит в карточке сдачи — открытой, ролик
    очередь не держит. Так её и нажимали, не сдав работу."""
    from app.models.task_block import BLOCK_UPLOAD, BLOCK_VIDEO

    task = _task(db, user, title="Перспектива", kind="material", day=today_msk())
    task.due_at = day_bounds(today_msk())[1] - timedelta(minutes=1)
    video = TaskBlock(
        task_id=task.id, block_type=BLOCK_VIDEO, title="Разбор работ",
        sort_order=0, is_required=False,
    )
    upload = TaskBlock(
        task_id=task.id, block_type=BLOCK_UPLOAD, body="Сдай работу",
        sort_order=1, is_required=True,
    )
    db.add_all([upload, video])
    db.commit()
    return task, upload


def test_completion_button_waits_for_required_steps(auth_client, db):
    """Владелец 01.10.2026: «Завершить задание закрывает, даже когда задания не
    выполнены». Ученик закрывал задание с несданной обязательной работой и
    снимал этим долг цикла. Теперь сервер отказывает, пока она не сдана;
    необязательный ролик проход не держит, и сданная работа закрывает задание
    сама (11.10.2026)."""
    from app.models.tracker import TrackerTaskState

    client, user = auth_client
    task, upload = _task_with_required_and_optional_step(db, user)

    blocked = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")
    assert blocked.status_code == 409

    close_block_for_user(db, upload, user.id, source="upload")
    db.commit()

    state = db.query(TrackerTaskState).filter_by(task_id=task.id, user_id=user.id).one()
    assert state.status == "done" and state.completion_source == "required_done"


def test_completion_button_ignores_a_required_step_that_closed_for_good(auth_client, db):
    """Обязательный шаг, закрытый по календарю, сделать уже нечем — кнопку он не
    держит, как не держит и очередь ленты (`holds_sequence`). Иначе тупик."""
    client, user = auth_client
    task, upload = _task_with_required_and_optional_step(db, user)
    upload.closes_at = now_msk() - timedelta(hours=1)
    db.commit()

    toggle = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")
    assert toggle.status_code == 200, toggle.text


def test_closed_task_counts_its_text_and_link_as_done(auth_client, db, rendered):
    """Аудит АОП ученика, находка 3 (владелец 01.10.2026, вариант «а»): текст и
    ссылку отметить нечем, поэтому они «сделаны», когда закрыто задание. До этого
    задание из текста и ссылки, закрытое кнопкой, давало «Сделано 0 из 2»."""
    from app.models.task_block import BLOCK_LINK

    client, user = auth_client
    task = _task(db, user, title="Вводный материал", kind="material", day=today_msk())
    task.due_at = day_bounds(today_msk())[1] - timedelta(minutes=1)
    _block(db, task, title="Как строить", order=0, is_required=False)
    db.add(TaskBlock(
        task_id=task.id, block_type=BLOCK_LINK, title="Разбор", url="https://example.com",
        sort_order=1, is_required=False,
    ))
    db.commit()

    feed = _feed(client, rendered)["feed"]
    assert (feed["done_count"], feed["total_count"]) == (0, 2)

    closed = client.post(f"/cabinet/tracker/tasks/{task.id}/toggle")
    assert closed.status_code == 200, closed.text
    feed = _feed(client, rendered)["feed"]
    assert (feed["done_count"], feed["total_count"]) == (2, 2)
    assert [step["status"] for step in feed["steps"]] == [STATUS_DONE, STATUS_DONE]


def test_learning_cycle_period_widens_the_window(auth_client, db):
    """Трёхнедельный цикл показывает задание, которое в календарную неделю не
    попадает, — ради этого период и заводился."""
    client, user = auth_client
    _topic(db, user, title="Цикл предобучения", opens_in_days=-3, ends_in_days=11)
    _task(db, user, title="Через десять дней", day=today_msk() + timedelta(days=10))

    resp = client.get("/cabinet/learning")
    assert "Через десять дней" in resp.text

# ── возврат в пройденные циклы (владелец 03.09.2026) ────────────────────────

def test_learning_lists_past_cycles(auth_client, db):
    """«Он может вернуться в этот цикл, потому что у каждого цикла своя тема»."""
    client, user = auth_client
    _topic(db, user, title="Первый цикл", opens_in_days=-20, ends_in_days=-10)
    _topic(db, user, title="Второй цикл", opens_in_days=-3, ends_in_days=10)

    resp = client.get("/cabinet/learning")

    assert "Первый цикл" in resp.text
    assert "Второй цикл" in resp.text


def test_learning_opens_a_past_cycle_read_only(auth_client, db, rendered):
    client, user = auth_client
    past = _topic(db, user, title="Первый цикл", opens_in_days=-20, ends_in_days=-10)
    _topic(db, user, title="Второй цикл", opens_in_days=-3, ends_in_days=10)

    assert _feed(client, rendered, f"/cabinet/learning?cycle={past.id}")["feed"]["is_archive"] is True


def test_learning_ignores_a_future_cycle_in_the_link(auth_client, db, rendered):
    """Не начавшийся цикл вперёд не выдаётся, даже если номер подобрали руками."""
    client, user = auth_client
    future = _topic(db, user, title="Будущий цикл", opens_in_days=5, ends_in_days=15)

    assert not _feed(client, rendered, f"/cabinet/learning?cycle={future.id}")["feed"]["is_archive"]


# ── подпись шага: ровно одна (16.09.2026) ───────────────────────────────────


def test_feed_still_names_a_task_without_blocks(auth_client, db):
    """Задание без блоков идёт одной карточкой — подпись у неё остаётся."""
    client, user = auth_client
    _task(db, user, title="Сдать композицию")

    resp = client.get("/cabinet/learning")

    assert "Сдать композицию" in resp.text


def test_learning_closed_block_is_locked_as_closed(auth_client, db, rendered):
    """Блок, доступ к которому закрыт по календарю, ждёт не очереди и не даты:
    подпись у него своя — текст куратора или «Доступ закрыт»."""
    client, user = auth_client
    task = _task(db, user, title="Задание")
    block = _block(db, task, title="Модуль", order=1, is_required=False)
    block.closes_at = now_msk().astimezone(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    db.commit()

    step = _steps(_feed(client, rendered))["Модуль"]
    assert step["status"] == STATUS_LOCKED
    assert step["lock_reason"] == LOCK_BY_CLOSED
