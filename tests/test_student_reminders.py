"""Напоминания ученику по расписанию (владелец 29.09.2026).

`services/student_reminders.py`: новое задание, новое видео, срок сдачи
(за сутки и за 3 часа), конец доступа (за 3 дня и за сутки). Одно
уведомление на задание, несколько новых заданий — одной сводкой, повторов
нет (`StudentReminder`).
"""
from datetime import datetime, timedelta, timezone

from app.models.homework_submission import HomeworkSubmission
from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.learning_video import LearningVideo
from app.models.notification import Notification
from app.models.task_block import TaskBlock, TaskBlockState, TaskBlockTariff
from app.models.tracker import ITEM_HOMEWORK, ITEM_MOCK_EXAM, SOURCE_HOMEWORK
from app.services.student_reminders import run_student_reminders
from app.services.tracker import create_homework, create_task

NOW = datetime.now(timezone.utc)


def _naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _owner(user_factory):
    return user_factory(vk_id=800_001, name="ГП", role_name="админ")


def _topic(db, owner, *, opens_at, ends_at=None):
    topic = LearningTopic(
        title="Цикл",
        opens_at=_naive(opens_at),
        ends_at=_naive(ends_at or NOW + timedelta(days=7)),
        assign_to_all=True,
        is_published=True,
        kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    return topic


def _task(db, owner, topic, *, title="Натюрморт", created_at=None, **kwargs):
    task = create_task(
        db, title=title, user_id=owner.id, kind=kwargs.pop("kind", "material"),
        topic_id=topic.id, assign_to_all=True, **kwargs,
    )
    task.is_published = True
    if created_at is not None:
        task.created_at = _naive(created_at)
    db.commit()
    return task


def _video(db):
    video = LearningVideo(
        bunny_library_id=1, bunny_video_id="lesson", title="Урок",
        status="ready", is_published=True,
    )
    db.add(video)
    db.commit()
    return video


def _block(db, task, block_type="photo_upload", *, created_at=None, **kwargs):
    block = TaskBlock(task_id=task.id, block_type=block_type, title="Шаг", **kwargs)
    if created_at is not None:
        block.created_at = _naive(created_at)
    db.add(block)
    db.commit()
    return block


def _old_task(db, owner, **kwargs):
    """Задание, открытое два дня назад, — о нём уже не пишут."""
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=3))
    return _task(db, owner, topic, created_at=NOW - timedelta(days=2), **kwargs)


def _run(db, now=None):
    # «Сейчас» — момент вызова, а не загрузки файла: в полном прогоне между
    # ними проходят минуты, и только что созданное задание оказалось бы
    # «из будущего» относительно NOW.
    run_student_reminders(db, now=now or datetime.now(timezone.utc) + timedelta(minutes=1))


def _notes(db, user):
    return (
        db.query(Notification)
        .filter(Notification.user_id == user.id)
        .order_by(Notification.id)
        .all()
    )


# ── Новое задание и видео ───────────────────────────────────────────────────

def test_new_task_is_announced_once(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_002, name="Аня")
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    _task(db, owner, topic, title="Куб в перспективе")

    _run(db)
    _run(db)

    notes = _notes(db, student)
    assert [n.title for n in notes] == ["Новое задание: «Куб в перспективе»"]


def test_task_with_video_is_one_video_lesson(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_003, name="Боря")
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    task = _task(db, owner, topic, title="Светотень")
    _block(db, task, "video", video_id=_video(db).id)

    _run(db)

    assert [n.title for n in _notes(db, student)] == ["Новый видеоурок: «Светотень»"]


def test_video_announced_with_its_task_is_not_repeated(db, user_factory):
    """Следующий прогон видит тот же ролик в окне поиска — он уже был в
    задании, когда о нём написали, второго сообщения быть не должно."""
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_014, name="Нора")
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    task = _task(db, owner, topic, title="Светотень")
    _block(db, task, "video", video_id=_video(db).id, created_at=NOW - timedelta(minutes=5))

    _run(db)
    _run(db, now=datetime.now(timezone.utc) + timedelta(minutes=30))

    assert [n.title for n in _notes(db, student)] == ["Новый видеоурок: «Светотень»"]


def test_video_opening_tomorrow_comes_tomorrow(db, user_factory):
    """Видео загружено, но открывается завтра: сегодня — «Новое задание»,
    видео — отдельным сообщением в момент открытия, не раньше."""
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_015, name="Оля")
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    task = _task(db, owner, topic, title="Разбор")
    opens = datetime.now(timezone.utc) + timedelta(days=1)
    _block(db, task, "video", video_id=_video(db).id, opens_at=_naive(opens))

    _run(db)
    assert [n.title for n in _notes(db, student)] == ["Новое задание: «Разбор»"]

    _run(db, now=opens - timedelta(minutes=10))
    assert len(_notes(db, student)) == 1

    _run(db, now=opens + timedelta(minutes=10))
    assert [n.title for n in _notes(db, student)] == [
        "Новое задание: «Разбор»",
        "Новое видео в задании «Разбор»",
    ]


def test_task_opening_tomorrow_comes_tomorrow_as_video_lesson(db, user_factory):
    """Всё задание с видео открывается завтра — одно сообщение завтра."""
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_016, name="Паша")
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    opens = datetime.now(timezone.utc) + timedelta(days=1)
    task = _task(db, owner, topic, title="Лекция", starts_at=_naive(opens))
    _block(db, task, "video", video_id=_video(db).id)

    _run(db)
    assert _notes(db, student) == []

    _run(db, now=opens + timedelta(minutes=10))
    assert [n.title for n in _notes(db, student)] == ["Новый видеоурок: «Лекция»"]


def test_old_task_is_silent(db, user_factory):
    """Первый запуск не заваливает учеников тем, что открылось давно."""
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_004, name="Вера")
    _old_task(db, owner)

    _run(db)

    assert _notes(db, student) == []


def test_task_waits_for_its_topic_to_open(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_005, name="Гоша")
    topic = _topic(db, owner, opens_at=NOW + timedelta(days=1))
    _task(db, owner, topic)

    _run(db)

    assert _notes(db, student) == []


def test_task_waits_for_its_own_start(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_006, name="Даша")
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    _task(db, owner, topic, starts_at=NOW + timedelta(hours=5))

    _run(db)

    assert _notes(db, student) == []


def test_cycle_opening_with_many_tasks_is_one_summary(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_007, name="Егор")
    topic = _topic(db, owner, opens_at=NOW - timedelta(minutes=10))
    for title in ("Куб", "Шар", "Цилиндр"):
        _task(db, owner, topic, title=title, created_at=NOW - timedelta(days=2))

    _run(db)

    notes = _notes(db, student)
    assert len(notes) == 1
    assert notes[0].title == "Новое в ленте обучения: 3"
    assert "«Куб»" in notes[0].text and "«Цилиндр»" in notes[0].text


def test_video_added_to_old_task_is_announced(db, user_factory):
    """Запись занятия, доложенная в открытое задание, — отдельное сообщение."""
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_008, name="Женя")
    task = _old_task(db, owner, title="Групповое занятие")
    _block(db, task, "video", video_id=_video(db).id)

    _run(db)
    _run(db)

    assert [n.title for n in _notes(db, student)] == [
        "Новое видео в задании «Групповое занятие»",
    ]


def test_video_added_after_the_task_was_announced(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_009, name="Зоя")
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    task = _task(db, owner, topic, title="Композиция")
    _run(db)

    later = datetime.now(timezone.utc) + timedelta(minutes=20)
    _block(db, task, "video", video_id=_video(db).id, created_at=later)
    _run(db, now=later + timedelta(minutes=5))

    assert [n.title for n in _notes(db, student)] == [
        "Новое задание: «Композиция»",
        "Новое видео в задании «Композиция»",
    ]


def test_task_of_another_tariff_is_silent(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_010, name="Игорь", tariff="УВЕРЕННЫЙ")
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    task = _task(db, owner, topic)
    block = _block(db, task, "video", video_id=_video(db).id)
    db.add(TaskBlockTariff(block_id=block.id, tariff="Я С ВАМИ"))
    db.commit()

    _run(db)

    assert _notes(db, student) == []


def test_mock_exam_item_is_left_to_its_own_reminder(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_011, name="Кира")
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    _task(db, owner, topic, kind=ITEM_MOCK_EXAM)

    _run(db)

    assert _notes(db, student) == []


def test_student_outside_the_group_is_silent(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_012, name="Лёва", is_group_member=False)
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    _task(db, owner, topic)

    _run(db)

    assert _notes(db, student) == []


def test_archived_student_is_silent(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_013, name="Мила")
    student.archived_at = _naive(NOW - timedelta(days=1))
    db.commit()
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    _task(db, owner, topic)

    _run(db)

    assert _notes(db, student) == []



def test_service_accounts_get_no_scheduled_reminders(db, user_factory, monkeypatch):
    """Служебные аккаунты (владелец 29.09.2026): к «службе заботы» привязан
    рабочий Telegram Лизы — ни новых заданий, ни конца доступа ей. Отбор
    живёт в `tracker.program_students` (вынесен 03.10.2026)."""
    import app.services.tracker as tracker_module

    owner = _owner(user_factory)
    care = user_factory(vk_id=800_017, name="служба заботы")
    care.access_until = _naive(NOW + timedelta(days=2))
    db.commit()
    monkeypatch.setattr(tracker_module, "REPORT_EXCLUDED_USER_IDS", frozenset({care.id}))
    topic = _topic(db, owner, opens_at=NOW - timedelta(days=1))
    _task(db, owner, topic)

    _run(db)

    assert _notes(db, care) == []

# ── Срок сдачи ──────────────────────────────────────────────────────────────

def test_deadline_reminders_a_day_and_three_hours_before(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_020, name="Нина")
    task = _old_task(db, owner, title="Портрет")
    deadline = NOW + timedelta(hours=20)
    _block(db, task, submit_until=_naive(deadline))

    _run(db)
    _run(db)
    _run(db, now=deadline - timedelta(hours=2))

    notes = _notes(db, student)
    assert [n.text for n in notes] == [
        "Осталось меньше суток.", "Осталось меньше трёх часов.",
    ]
    # Дату срока напоминание не называет (владелец 02.10.2026): её
    # называет только текст задания.
    assert notes[0].title == "Скоро закроется приём работ: «Портрет»"


def test_no_deadline_reminder_once_handed_in(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_021, name="Олег")
    task = _old_task(db, owner)
    block = _block(db, task, submit_until=_naive(NOW + timedelta(hours=20)))
    db.add(TaskBlockState(block_id=block.id, user_id=student.id, status="done"))
    db.commit()

    _run(db)

    assert _notes(db, student) == []


def test_extended_deadline_reminds_again(db, user_factory):
    """Продлили срок — ключ другой, напоминание придёт заново."""
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_022, name="Петя")
    task = _old_task(db, owner)
    block = _block(db, task, submit_until=_naive(NOW + timedelta(hours=20)))
    _run(db)

    block.submit_until = _naive(NOW + timedelta(hours=23))
    db.commit()
    _run(db)

    assert len(_notes(db, student)) == 2


def test_task_level_deadline_reaches_blocks(db, user_factory):
    """Срок задания действует на блоки без своего — та же функция, что
    запирает сдачу (`submission_edit.upload_deadline`)."""
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_023, name="Рита")
    task = _old_task(db, owner)
    task.submit_until = _naive(NOW + timedelta(hours=2))
    db.commit()
    _block(db, task)

    _run(db)

    assert [n.text for n in _notes(db, student)] == ["Осталось меньше трёх часов."]


def test_video_only_task_has_nothing_to_remind(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_024, name="Саша")
    task = _old_task(db, owner, due_at=_naive(NOW + timedelta(hours=5)))
    _block(db, task, "video", video_id=_video(db).id, created_at=NOW - timedelta(days=2))

    _run(db)

    assert _notes(db, student) == []


def test_homework_deadline(db, user_factory):
    owner = _owner(user_factory)
    student = user_factory(vk_id=800_025, name="Таня")
    handed = user_factory(vk_id=800_026, name="Уля")
    homework = create_homework(db, title="Домашка", user_id=owner.id)
    task = _old_task(
        db, owner, title="Домашка", kind=ITEM_HOMEWORK,
        source_kind=SOURCE_HOMEWORK, source_id=homework.id,
    )
    task.submit_until = _naive(NOW + timedelta(hours=10))
    db.add(HomeworkSubmission(
        homework_id=homework.id, tracker_task_id=task.id, user_id=handed.id,
        submitted_at=_naive(NOW - timedelta(hours=1)),
    ))
    db.commit()

    _run(db)

    assert [n.text for n in _notes(db, student)] == ["Осталось меньше суток."]
    assert _notes(db, handed) == []


# ── Конец доступа ───────────────────────────────────────────────────────────

def test_access_ending_in_three_days_then_last_day(db, user_factory):
    student = user_factory(vk_id=800_030, name="Федя")
    until = NOW + timedelta(days=2, hours=12)
    student.access_until = _naive(until)
    db.commit()

    _run(db)
    _run(db)
    _run(db, now=until - timedelta(hours=10))

    notes = _notes(db, student)
    assert [n.text for n in notes] == [
        "Осталось три дня. Оплати обучение, и уроки останутся открытыми.",
        "Остался последний день. Оплати обучение, и уроки останутся открытыми.",
    ]
    assert notes[0].title.startswith("Доступ к урокам закроется ")


def test_unlimited_access_is_silent(db, user_factory):
    student = user_factory(vk_id=800_031, name="Хасан")

    _run(db)

    assert _notes(db, student) == []


# ── Планировщик — один на все воркеры ───────────────────────────────────────

def test_scheduler_lock_is_taken_once(tmp_path, monkeypatch):
    """`--workers 4` на проде: планировщик держит только первый воркер,
    иначе каждое напоминание уходило бы вчетверо."""
    import pytest

    pytest.importorskip("fcntl")
    import app.services.exam_scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "SCHEDULER_LOCK_PATH", str(tmp_path / "lock"))
    monkeypatch.setattr(scheduler_module, "_scheduler_lock_file", None)
    assert scheduler_module._acquire_scheduler_lock() is True
    holder = scheduler_module._scheduler_lock_file

    # Второй «воркер» — другой дескриптор того же файла.
    monkeypatch.setattr(scheduler_module, "_scheduler_lock_file", None)
    assert scheduler_module._acquire_scheduler_lock() is False
    holder.close()
