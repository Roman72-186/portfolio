"""Тесты страницы «Статистика активности» (/cabinet/superadmin/activity)
и сервиса app/services/activity_stats.py."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditLog
from app.models.curator_report import CuratorReport
from app.models.exam_cycle import ExamCycle
from app.models.notification import Notification
from app.models.work import Work, WORK_TYPE_MOCK_EXAM


@pytest.fixture()
def superadmin_client(client, db, user_factory, session_factory):
    user = user_factory(vk_id=970001, name="Super Admin", role_name="суперадмин")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    return client, user


# ── Сервис ────────────────────────────────────────────────────────────────────

def test_fmt_duration():
    from app.services.activity_stats import fmt_duration

    assert fmt_duration(None) is None
    assert fmt_duration(300) == "5 м."
    assert fmt_duration(4500) == "1 ч. 15 м."
    assert fmt_duration(90000) == "1 д. 1 ч."


def test_login_stats_counts_and_inactive(db, user_factory):
    from app.services.activity_stats import get_login_stats

    now = datetime.now(timezone.utc)
    fresh = user_factory(vk_id=970101, name="Свежий", role_name="ученик")
    fresh.last_login_at = now - timedelta(days=1)
    stale = user_factory(vk_id=970102, name="Старый", role_name="ученик")
    stale.last_login_at = now - timedelta(days=20)
    never = user_factory(vk_id=970103, name="Никогда", role_name="ученик")
    db.commit()

    stats = get_login_stats(db)
    assert stats["total"] == 3
    assert stats["d7"] == 1
    assert stats["d30"] == 2
    inactive_names = [s["student_name"] for s in stats["inactive"]]
    assert "Никогда" in " ".join(inactive_names) or any("Никогда" in n for n in inactive_names)
    assert not any("Свежий" in n for n in inactive_names)


def test_curator_review_speed_aggregates(db, user_factory):
    from app.services.activity_stats import get_curator_review_speed

    curator = user_factory(vk_id=970201, name="Куратор Проверяющий", role_name="куратор")
    student = user_factory(vk_id=970202, name="Ученик", role_name="ученик")
    created = datetime.now(timezone.utc) - timedelta(hours=2)
    db.add(Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="июнь", year=2026,
        filename="w.jpg", status="success", score=80,
        created_at=created, scored_at=created + timedelta(hours=1),
        scored_by_id=curator.id,
    ))
    db.commit()

    rows = get_curator_review_speed(db)
    assert len(rows) == 1
    assert rows[0]["scored_count"] == 1
    assert rows[0]["avg_score"] == 80.0
    # ~1 час на проверку
    assert 3500 <= rows[0]["avg_review_seconds"] <= 3700


def test_revision_stats_pending_and_avg(db, user_factory):
    from app.services.activity_stats import get_revision_stats

    student = user_factory(vk_id=970301, name="Ревизия Ученик", role_name="ученик")
    now = datetime.now(timezone.utc)
    db.add(ExamCycle(
        user_id=student.id, subject="Рисунок", started_at=date.today(),
        revision_requested_at=now - timedelta(hours=3),
    ))
    db.add(ExamCycle(
        user_id=student.id, subject="Композиция", started_at=date.today(),
        revision_requested_at=now - timedelta(hours=5),
        revision_done_at=now - timedelta(hours=3),
    ))
    db.commit()

    stats = get_revision_stats(db)
    assert len(stats["pending"]) == 1
    assert stats["pending"][0]["subject"] == "Рисунок"
    assert stats["done_count"] == 1
    # правка заняла ~2 часа
    assert 7100 <= stats["avg_fix_seconds"] <= 7300


def test_notification_reaction(db, user_factory):
    from app.services.activity_stats import get_notification_reaction

    student = user_factory(vk_id=970401, name="Уведомляемый", role_name="ученик")
    now = datetime.now(timezone.utc)
    db.add(Notification(
        user_id=student.id, title="Прочитанное",
        is_read=True, created_at=now - timedelta(minutes=30), read_at=now,
    ))
    db.add(Notification(user_id=student.id, title="Непрочитанное", is_read=False))
    db.commit()

    stats = get_notification_reaction(db)
    assert stats["read_count"] == 1
    assert stats["unread_count"] == 1
    assert 1700 <= stats["avg_reaction_seconds"] <= 1900  # ~30 минут


def test_mock_attempt_stats(db, user_factory):
    from app.services.activity_stats import get_mock_attempt_stats
    from app.models.mock_exam_attempt import MockExamAttempt

    student = user_factory(vk_id=970601, name="Пробник Ученик", role_name="ученик")
    now = datetime.now(timezone.utc)
    db.add(MockExamAttempt(
        user_id=student.id, subject="Рисунок", ticket_title="Б1",
        started_at=now - timedelta(hours=3), completed_at=now - timedelta(hours=1),
    ))
    db.add(MockExamAttempt(
        user_id=student.id, subject="Композиция", ticket_title="Б2",
        started_at=now - timedelta(days=1), expired_at=now - timedelta(hours=20),
    ))
    db.commit()

    stats = get_mock_attempt_stats(db)
    assert stats["total"] == 2
    assert stats["completed_count"] == 1
    assert stats["expired_count"] == 1
    assert 7100 <= stats["avg_seconds"] <= 7300  # ~2 часа
    assert stats["by_subject"]["Рисунок"]["completed"] == 1


def test_cycle_duration_stats(db, user_factory):
    from app.services.activity_stats import get_cycle_duration_stats

    student = user_factory(vk_id=970701, name="Цикл Ученик", role_name="ученик")
    now = datetime.now(timezone.utc)
    db.add(ExamCycle(user_id=student.id, subject="Рисунок", started_at=date.today()))
    db.add(ExamCycle(
        user_id=student.id, subject="Композиция",
        started_at=date.today() - timedelta(days=3), closed_at=now,
    ))
    db.commit()

    stats = get_cycle_duration_stats(db)
    assert stats["open_count"] == 1
    assert stats["closed_count"] == 1
    assert stats["avg_seconds"] > 2 * 86400  # цикл длился ~3 дня


def test_feedback_curator_stats(db, user_factory):
    from app.services.activity_stats import get_feedback_curator_stats
    from app.models.feedback import Feedback, FeedbackMessage

    curator = user_factory(vk_id=970801, name="Куратор ОС", role_name="куратор")
    student = user_factory(vk_id=970802, name="Ученик ОС", role_name="ученик")
    now = datetime.now(timezone.utc)
    work = Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="июль", year=2026,
        filename="w.jpg", status="success", created_at=now - timedelta(hours=6),
    )
    db.add(work)
    db.flush()
    fb = Feedback(work_id=work.id, curator_id=curator.id)
    db.add(fb)
    db.flush()
    db.add(FeedbackMessage(
        feedback_id=fb.id, sender_id=curator.id, sender_role="curator",
        text="Первая ОС", created_at=now - timedelta(hours=3),
    ))
    db.add(FeedbackMessage(
        feedback_id=fb.id, sender_id=student.id, sender_role="student",
        text="Ответ", created_at=now - timedelta(hours=2),
    ))
    db.commit()

    rows = get_feedback_curator_stats(db)
    assert len(rows) == 1
    assert rows[0]["dialogs"] == 1
    assert rows[0]["avg_messages"] == 2.0
    # от загрузки работы до первой ОС ~3 часа
    assert 10700 <= rows[0]["avg_first_response_seconds"] <= 10900


def test_login_link_stats(db, user_factory):
    from app.services.activity_stats import get_login_link_stats
    from app.models.login_token import LoginToken

    student = user_factory(vk_id=970901, name="Линк Ученик", role_name="ученик")
    now = datetime.now(timezone.utc)
    db.add(LoginToken(
        user_id=student.id, token_hash="a" * 64,
        created_at=now - timedelta(minutes=10), used_at=now,
        expires_at=now + timedelta(days=1),
    ))
    db.add(LoginToken(
        user_id=student.id, token_hash="b" * 64,
        created_at=now - timedelta(days=2), revoked_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=1),
    ))
    db.commit()

    stats = get_login_link_stats(db)
    assert stats["total"] == 2
    assert stats["used_count"] == 1
    assert stats["revoked_count"] == 1
    assert stats["conversion_pct"] == 50.0
    assert 550 <= stats["avg_seconds"] <= 650  # ~10 минут


def test_self_score_stats(db, user_factory):
    from app.services.activity_stats import get_self_score_stats

    student = user_factory(vk_id=971001, name="Самооценка", role_name="ученик")
    db.add(Work(
        user_id=student.id, work_type="retake", month="июль", year=2026,
        filename="r.jpg", status="success", student_score=90, score=80,
    ))
    db.add(Work(
        user_id=student.id, work_type="retake", month="июль", year=2026,
        filename="r2.jpg", status="success", student_score=70, score=80,
    ))
    db.commit()

    stats = get_self_score_stats(db)
    assert stats["count"] == 2
    assert stats["avg_student"] == 80.0
    assert stats["avg_curator"] == 80.0
    assert stats["avg_diff"] == 0.0
    assert stats["student_higher_pct"] == 50.0


def test_retake_stats(db, user_factory):
    from app.services.activity_stats import get_retake_stats

    student = user_factory(vk_id=971101, name="Отработка", role_name="ученик")
    db.add(Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="июль", year=2026,
        filename="m.jpg", status="success", sent_to_retake=True, attempt_number=1,
    ))
    db.add(Work(
        user_id=student.id, work_type="retake", month="июль", year=2026,
        filename="r.jpg", status="success",
    ))
    db.commit()

    stats = get_retake_stats(db)
    assert stats["mock_total"] == 1
    assert stats["retake_total"] == 1
    assert stats["sent_to_retake"] == 1
    assert stats["sent_to_retake_pct"] == 100.0


# ── Роут ──────────────────────────────────────────────────────────────────────

def test_activity_page_renders_with_data(superadmin_client, db, user_factory):
    client, sa = superadmin_client
    curator = user_factory(vk_id=970501, name="Куратор Быстрый", role_name="куратор")
    student = user_factory(vk_id=970502, name="Ученик Активный", role_name="ученик")
    now = datetime.now(timezone.utc)
    student.last_login_at = now - timedelta(days=1)

    created = now - timedelta(hours=4)
    db.add(Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="июль", year=2026,
        filename="w.jpg", status="success", score=75,
        created_at=created, scored_at=created + timedelta(hours=2),
        scored_by_id=curator.id,
    ))
    db.add(ExamCycle(
        user_id=student.id, subject="Рисунок", started_at=date.today(),
        revision_requested_at=now - timedelta(hours=1),
    ))
    db.add(AuditLog(
        action="curator_assign", performed_by_id=sa.id,
        target_user_id=student.id, details="curator: — → 5",
    ))
    db.add(AuditLog(
        action="tariff_change", performed_by_id=student.id,
        target_user_id=student.id, details="tariff: УВЕРЕННЫЙ → МАКСИМУМ",
    ))
    db.add(CuratorReport(
        curator_id=curator.id, video_url="https://s3.example/report.mp4",
        viewed_at=now, viewed_by_id=sa.id,
    ))
    db.commit()

    resp = client.get("/cabinet/superadmin/activity")
    assert resp.status_code == 200
    assert "Статистика активности" in resp.text
    assert "Скорость проверки работ" in resp.text
    assert "Куратор Быстрый" in resp.text
    assert "Возвраты обратной связи на правку" in resp.text
    assert "Ученик Активный" in resp.text
    assert "Смена куратора" in resp.text
    assert "Смена тарифа" in resp.text
    assert "curator: — → 5" in resp.text
    # Новые секции
    assert "Обратная связь кураторов" in resp.text
    assert "Поведение на пробнике" in resp.text
    assert "Циклы Пробника" in resp.text
    assert "Пересдачи и доработки" in resp.text
    assert "Самооценка vs оценка куратора" in resp.text
    assert "Одноразовые ссылки для входа" in resp.text


def test_activity_page_forbidden_for_student(auth_client):
    client, _ = auth_client
    resp = client.get("/cabinet/superadmin/activity")
    assert resp.status_code == 403


def test_activity_page_renders_empty(superadmin_client):
    client, _ = superadmin_client
    resp = client.get("/cabinet/superadmin/activity")
    assert resp.status_code == 200
    assert "Оценённых работ пока нет" in resp.text
    assert "Нет циклов, ожидающих правки" in resp.text


# ── Вкладки ролей и новые метрики (владелец 25.09.2026) ──────────────────────

def test_role_group_maps_ranks_to_tabs():
    from app.services.activity_stats import role_group

    assert role_group(1) is None
    assert role_group(2) == "curators"
    # Модератор работает с правами Главного преподавателя — одна вкладка.
    assert role_group(3) == "head"
    assert role_group(4) == "head"
    assert role_group(5) == "superadmin"


def test_diagnostic_is_the_first_row_of_the_page(superadmin_client):
    client, _ = superadmin_client
    text = client.get("/cabinet/superadmin/activity").text
    diag = text.index("Диагностика АРХИ-ПРОФИЛЯ")
    for later in ("Заходили за 7 дней", "Действия учеников", "Просмотр видео",
                  "Поведение на пробнике", "Скорость проверки работ", "Журнал изменений"):
        assert diag < text.index(later), later


def test_page_switches_roles_with_the_shared_nav_pill(superadmin_client):
    client, _ = superadmin_client
    text = client.get("/cabinet/superadmin/activity").text
    # Общий компонент из base.css, не свой набор кнопок страницы.
    assert 'class="nav-pill act-tabs"' in text
    for key in ("students", "curators", "head", "superadmin"):
        assert f'data-act-tab="{key}"' in text
    assert 'id="actPanelStudents">' in text
    for panel in ("actPanelCurators", "actPanelHead", "actPanelSuperadmin"):
        assert f'id="{panel}" hidden' in text


def test_student_metrics_ignore_staff_activity(db, user_factory):
    """Вход, ролик и задание сотрудника не попадают в ученические цифры:
    журнал входов, VideoProgress и TrackerTaskState пишутся у всех."""
    from app.models.activity_event import StudentActivityEvent
    from app.models.tracker import TrackerTask, TrackerTaskState
    from app.models.video_progress import VideoProgress
    from app.services.activity_stats import (
        get_student_event_stats, get_task_progress_stats, get_video_watch_stats,
    )

    student = user_factory(vk_id=971001, name="Ученик Смотрит", role_name="ученик")
    curator = user_factory(vk_id=971002, name="Куратор Смотрит", role_name="куратор")
    now = datetime.now(timezone.utc)
    task = TrackerTask(title="Композиция: этюд")
    db.add(task)
    db.flush()
    for u in (student, curator):
        db.add(StudentActivityEvent(user_id=u.id, event_type="login", created_at=now))
        db.add(VideoProgress(
            user_id=u.id, video_id="vid-1", position_seconds=50,
            duration_seconds=100, watched_seconds=50,
        ))
        db.add(TrackerTaskState(task_id=task.id, user_id=u.id, started_at=now))
    db.commit()

    events = get_student_event_stats(db)
    assert events["active_students"] == 1
    assert events["by_type"][0]["events"] == 1
    assert [t["student_name"] for t in events["top"]] == ["Ученик Смотрит"]

    video = get_video_watch_stats(db)
    assert video["students_watching"] == 1
    assert video["avg_share_pct"] == 50
    assert video["videos"][0]["title"] == "Ролик вне каталога"

    tasks = get_task_progress_stats(db)
    assert tasks["opened"] == 1
    assert tasks["stuck"] == 1


def test_task_progress_tells_who_closed_the_task(db, user_factory):
    from app.models.tracker import TrackerTask, TrackerTaskState
    from app.services.activity_stats import get_task_progress_stats

    curator = user_factory(vk_id=971101, name="Куратор", role_name="куратор")
    s1 = user_factory(vk_id=971102, name="Сам", role_name="ученик")
    s2 = user_factory(vk_id=971103, name="Система", role_name="ученик")
    s3 = user_factory(vk_id=971104, name="Преподаватель", role_name="ученик")
    now = datetime.now(timezone.utc)
    task = TrackerTask(title="Рисунок: куб")
    db.add(task)
    db.flush()
    for student, closer in ((s1, s1.id), (s2, None), (s3, curator.id)):
        db.add(TrackerTaskState(
            task_id=task.id, user_id=student.id, status="done",
            started_at=now - timedelta(hours=2), completed_at=now, completed_by_id=closer,
        ))
    db.commit()

    stats = get_task_progress_stats(db)
    assert stats["closed_by"] == {"student": 1, "system": 1, "staff": 1}
    assert stats["tasks"][0]["done"] == 3
    assert stats["tasks"][0]["avg_text"] == "2 ч. 0 м."


def test_submission_stats_counts_waiting_blocks(db, user_factory):
    from app.models.task_block import TaskBlock, TaskBlockSubmission
    from app.models.tracker import TrackerTask
    from app.services.activity_stats import get_submission_stats

    s1 = user_factory(vk_id=971201, name="Ждёт", role_name="ученик")
    s2 = user_factory(vk_id=971202, name="Проверен", role_name="ученик")
    now = datetime.now(timezone.utc)
    task = TrackerTask(title="Сдача")
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type="photo_upload")
    db.add(block)
    db.flush()
    db.add(TaskBlockSubmission(block_id=block.id, user_id=s1.id, submitted_at=now))
    db.add(TaskBlockSubmission(
        block_id=block.id, user_id=s2.id, submitted_at=now - timedelta(hours=3),
        reviewed_at=now - timedelta(hours=1), scored_at=now,
    ))
    db.commit()

    stats = get_submission_stats(db)
    assert stats["blocks_total"] == 2
    assert stats["blocks_waiting"] == 1
    assert stats["blocks_scored"] == 1
    # реакция — первое из просмотра и балла, то есть 2 часа
    assert stats["avg_reaction_text"] == "2 ч. 0 м."


def test_staff_activity_splits_roles_into_tabs(db, user_factory):
    from app.models.activity_event import StudentActivityEvent
    from app.models.feedback import Feedback, FeedbackMessage
    from app.services.activity_stats import get_staff_activity

    curator = user_factory(vk_id=971301, name="Куратор Работает", role_name="куратор")
    moderator = user_factory(vk_id=971302, name="Модератор", role_name="модератор")
    head = user_factory(vk_id=971303, name="Главный", role_name="админ")
    sa = user_factory(vk_id=971304, name="Супер", role_name="суперадмин")
    student = user_factory(vk_id=971305, name="Ученик", role_name="ученик")
    now = datetime.now(timezone.utc)
    work = Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="июль", year=2026,
        filename="w.jpg", status="success", score=80, scored_at=now, scored_by_id=curator.id,
    )
    db.add(work)
    db.flush()
    fb = Feedback(work_id=work.id, curator_id=curator.id)
    db.add(fb)
    db.flush()
    db.add(FeedbackMessage(feedback_id=fb.id, sender_id=curator.id, sender_role="curator", text="ОС"))
    db.add(FeedbackMessage(feedback_id=fb.id, sender_id=student.id, sender_role="student", text="Спасибо"))
    db.add(StudentActivityEvent(user_id=curator.id, event_type="login", created_at=now))
    db.add(AuditLog(action="tariff_change", performed_by_id=head.id, target_user_id=student.id))
    db.commit()

    rows = {r["name"].strip(): r for r in get_staff_activity(db)}
    assert "Ученик" not in rows
    assert rows["Куратор Работает"]["role_group"] == "curators"
    assert rows["Модератор"]["role_group"] == "head"
    assert rows["Главный"]["role_group"] == "head"
    assert rows["Супер"]["role_group"] == "superadmin"
    assert rows["Куратор Работает"]["works_scored"] == 1
    assert rows["Куратор Работает"]["messages"] == 1
    assert rows["Куратор Работает"]["logins"] == 1
    assert rows["Главный"]["actions"] == 1


def test_activity_page_puts_staff_rows_into_their_tabs(superadmin_client, db, user_factory):
    client, _ = superadmin_client
    user_factory(vk_id=971401, name="Куратор Вкладка", role_name="куратор")
    user_factory(vk_id=971402, name="Модератор Вкладка", role_name="модератор")
    text = client.get("/cabinet/superadmin/activity").text
    curators = text.index('id="actPanelCurators"')
    head = text.index('id="actPanelHead"')
    superadmin = text.index('id="actPanelSuperadmin"')
    assert curators < text.index("Куратор Вкладка") < head
    assert head < text.index("Модератор Вкладка") < superadmin


def test_block_check_is_credited_to_both_reviewer_and_scorer(db, user_factory):
    """Куратор открыл сдачу, ГП поставил балл — зачёт обоим; своя двойная
    отметка (посмотрел и оценил сам) — один раз."""
    from app.models.task_block import TaskBlock, TaskBlockSubmission
    from app.models.tracker import TrackerTask
    from app.services.activity_stats import get_staff_activity

    curator = user_factory(vk_id=971501, name="Куратор Смотрел", role_name="куратор")
    head = user_factory(vk_id=971502, name="Главный Оценил", role_name="админ")
    s1 = user_factory(vk_id=971503, name="Ученик Один", role_name="ученик")
    s2 = user_factory(vk_id=971504, name="Ученик Два", role_name="ученик")
    now = datetime.now(timezone.utc)
    task = TrackerTask(title="Сдача")
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type="photo_upload")
    db.add(block)
    db.flush()
    db.add(TaskBlockSubmission(
        block_id=block.id, user_id=s1.id, submitted_at=now,
        reviewed_by_id=curator.id, reviewed_at=now, scored_by_id=head.id, scored_at=now,
    ))
    db.add(TaskBlockSubmission(
        block_id=block.id, user_id=s2.id, submitted_at=now,
        reviewed_by_id=head.id, reviewed_at=now, scored_by_id=head.id, scored_at=now,
    ))
    db.commit()

    rows = {r["name"].strip(): r for r in get_staff_activity(db)}
    assert rows["Куратор Смотрел"]["blocks_checked"] == 1
    assert rows["Главный Оценил"]["blocks_checked"] == 2


def test_superadmin_reviews_show_up_on_his_tab(superadmin_client, db, user_factory):
    client, sa = superadmin_client
    student = user_factory(vk_id=971601, name="Ученик Оценён", role_name="ученик")
    now = datetime.now(timezone.utc)
    db.add(Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="июль", year=2026,
        filename="w.jpg", status="success", score=90,
        created_at=now - timedelta(hours=1), scored_at=now, scored_by_id=sa.id,
    ))
    db.commit()
    text = client.get("/cabinet/superadmin/activity").text
    panel = text[text.index('id="actPanelSuperadmin"'):]
    assert "Скорость проверки работ" in panel
    assert "Super Admin" in panel.split("Обратная связь")[0]
