"""Новенький не видит циклы, закончившиеся до его прихода.

Владелец 29.09.2026: новые ученики годового курса «не должны видеть
предобучение 1, 2, 3 цикл, так как они не платили за него», а текущий цикл,
«Портфолио» и всё дальнейшее — видеть. Граница — `User.program_access_from`,
правило — `video_topics.ended_before_arrival_filter`.
"""
import re
from datetime import datetime, timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_STAGE, TOPIC_KIND_WEEK, LearningTopic
from app.services.cycle_feed import feed_for_student
from app.services.cycle_stats import cycle_stats
from app.services.tracker import accessible_cycles, create_task, effective_cycle
from app.services.tz import msk_midnight, today_msk
from app.services.user_management import apply_tariff_change, unarchive_user
from app.services.video_topics import accessible_topic_ids, topic_audience_user_ids

TODAY = today_msk()


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _topic(db, owner, *, title, starts_on, ends_on, kind=TOPIC_KIND_WEEK, parent=None):
    topic = LearningTopic(
        title=title,
        opens_at=_utc(msk_midnight(starts_on)),
        ends_at=_utc(msk_midnight(ends_on) + timedelta(hours=23, minutes=59)),
        assign_to_all=True,
        is_published=True,
        kind=kind,
        created_by_id=owner.id,
        parent_id=parent.id if parent is not None else None,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _undated_task(db, owner, topic, *, title, is_required=True):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        topic_id=topic.id, assign_to_all=True, is_required=is_required,
    )
    task.is_published = True
    db.commit()
    return task


def _preparation(db, owner):
    """Прод 29.09.2026 в миниатюре: этап с «Портфолио», два закончившихся
    цикла и идущий."""
    stage = _topic(
        db, owner, title="Предобучение", kind=TOPIC_KIND_STAGE,
        starts_on=TODAY - timedelta(days=13), ends_on=TODAY + timedelta(days=5),
    )
    portfolio = _undated_task(db, owner, stage, title="Портфолио", is_required=False)
    ended_1 = _topic(
        db, owner, title="Цикл 1", parent=stage,
        starts_on=TODAY - timedelta(days=11), ends_on=TODAY - timedelta(days=3),
    )
    ended_2 = _topic(
        db, owner, title="Цикл 2", parent=stage,
        starts_on=TODAY - timedelta(days=6), ends_on=TODAY - timedelta(days=1),
    )
    _undated_task(db, owner, ended_2, title="Задание цикла 2")
    running = _topic(
        db, owner, title="Цикл 4", parent=stage,
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=5),
    )
    _undated_task(db, owner, running, title="Задание цикла 4")
    return stage, portfolio, ended_1, ended_2, running


def _arrive_today(db, user):
    user.program_access_from = datetime.now(timezone.utc)
    db.commit()


def test_newcomer_sees_only_the_running_cycle_and_the_stage(db, regular_user):
    stage, _, ended_1, ended_2, running = _preparation(db, regular_user)
    _arrive_today(db, regular_user)

    visible = accessible_topic_ids(db, regular_user.id)

    assert ended_1.id not in visible
    assert ended_2.id not in visible
    assert running.id in visible
    assert stage.id in visible


def test_student_without_the_mark_sees_everything(db, regular_user):
    """Все, кто учился до 29.09.2026, живут с пустой отметкой — им ничего не
    прячется."""
    stage, _, ended_1, ended_2, running = _preparation(db, regular_user)

    visible = accessible_topic_ids(db, regular_user.id)

    assert {stage.id, ended_1.id, ended_2.id, running.id} <= visible


def test_newcomer_is_not_a_debtor_of_a_cycle_he_never_saw(db, regular_user):
    """Главный сценарий: без правила новенький открывал ленту на цикле 2 —
    обязательное задание там не сдано, и лента вела к «долгу»."""
    _, portfolio, _, ended_2, running = _preparation(db, regular_user)
    _arrive_today(db, regular_user)

    assert effective_cycle(db, regular_user.id, TODAY).id == running.id
    assert [c.id for c in accessible_cycles(db, regular_user.id)] == [running.id]

    feed = feed_for_student(db, user_id=regular_user.id, user_tariff=None, today=TODAY)

    assert feed["topic"].id == running.id
    assert [c["id"] for c in feed["cycles"]] == [running.id]
    assert feed["pinned_tasks"] == [
        {"id": portfolio.id, "title": "Портфолио", "is_current": False}
    ]


def test_cycle_that_ends_after_arrival_stays_visible(db, regular_user):
    """Граница по концу цикла, не по началу: пришедший посреди цикла его видит
    — иначе новенький остался бы без текущего цикла и без «Портфолио»."""
    _, _, _, _, running = _preparation(db, regular_user)
    regular_user.program_access_from = _utc(msk_midnight(TODAY)) + timedelta(hours=12)
    db.commit()

    assert running.id in accessible_topic_ids(db, regular_user.id)


def test_late_student_is_not_in_the_audience_of_an_ended_cycle(db, regular_user, user_factory):
    _, _, _, ended_2, running = _preparation(db, regular_user)
    newcomer = user_factory(vk_id=100_002, name="Новенький")
    _arrive_today(db, newcomer)

    assert newcomer.id not in topic_audience_user_ids(db, ended_2.id)
    assert newcomer.id in topic_audience_user_ids(db, running.id)
    assert regular_user.id in topic_audience_user_ids(db, ended_2.id)


def test_late_student_is_not_counted_in_ended_cycle_stats(db, regular_user, user_factory):
    """Статистика цикла не пишет новенького в «не сдали» цикла, которого он не
    видел."""
    _, _, _, ended_2, running = _preparation(db, regular_user)
    newcomer = user_factory(vk_id=100_002, name="Новенький")
    _arrive_today(db, newcomer)

    assert cycle_stats(db, ended_2)["students"] == 1
    assert cycle_stats(db, running)["students"] == 2


# ── кто ставит отметку ──────────────────────────────────────────────────────

def test_first_tariff_after_trial_opens_the_program_from_now(db, regular_user):
    """Пробник по ссылке `/proba` платит — программа открыта с дня оплаты, а
    не с того дня, когда он зашёл на пробу."""
    regular_user.tariff = ""
    regular_user.access_until = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    apply_tariff_change(db, regular_user.id, regular_user, "Я С ВАМИ")

    assert regular_user.program_access_from is not None
    assert regular_user.access_until is None


def test_tariff_switch_does_not_move_the_mark(db, regular_user):
    """Смена одного тарифа на другой — не приход: иначе ученик предобучения
    при переводе на другой тариф потерял бы пройденные циклы."""
    apply_tariff_change(db, regular_user.id, regular_user, "Я С ВАМИ")

    assert regular_user.program_access_from is None


def test_return_from_archive_opens_the_program_from_now(db, regular_user, admin_user):
    regular_user.archived_at = datetime.now(timezone.utc) - timedelta(days=100)
    regular_user.is_active = False
    db.commit()

    assert unarchive_user(db, regular_user.id, admin_user.id) is True

    db.refresh(regular_user)
    assert regular_user.program_access_from is not None


def test_new_telegram_account_gets_the_mark(db, role_factory):
    from app.api.auth import _upsert_telegram_user

    role_factory("ученик", 1)
    user, created = _upsert_telegram_user(
        db, chat_id=777_000_111, tg_from=None, is_group_member=True,
    )

    assert created is True
    assert user.program_access_from is not None


# ── трекер ведёт в ленту этапа ──────────────────────────────────────────────

def test_tracker_link_to_portfolio_opens_the_stage(auth_client, db):
    """«Перейти» у «Портфолио» в трекере: задания нет в ленте цикла, экран сам
    открывает ленту этапа."""
    client, user = auth_client
    stage, portfolio, _, _, _ = _preparation(db, user)

    response = client.get(f"/cabinet/learning?task={portfolio.id}")

    assert response.status_code == 200
    assert f'id="learning-task-{portfolio.id}"' in response.text
    chip = re.search(
        r'<a class="([^"]*)"[^>]*data-pinned-task-id="%d"' % portfolio.id, response.text,
    )
    assert chip is not None and "is-current" in chip.group(1)
