"""Должники цикла и «Напомнить всем» (владелец 30.09.2026).

Прецедент: цикл 3 предобучения не закрыли 30 учеников из 88 — задание
обязательное, а все его блоки необязательные, и само оно закрывается только
кнопкой «Завершить задание». Увидеть поимённо и напомнить можно было только
скриптом на проде. Теперь — экран статистики цикла, по тому же правилу, что
лента ученика (`tracker.missing_required_tasks`).
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic
from app.models.notification import Notification
from app.models.student_reminder import KIND_CYCLE_DEBT, StudentReminder
from app.models.task_block import BLOCK_TEXT, TaskBlock, TaskBlockTariff
from app.services.cycle_stats import cycle_debtors, cycle_stats, remind_cycle_debtors
from app.services.program import day_bounds
from app.services.tracker import close_task_for_user, create_task, is_cycle_complete
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cycle(db, owner, *, start=TODAY - timedelta(days=2), end=TODAY + timedelta(days=5),
           title="Цикл 3"):
    topic = LearningTopic(
        title=title, opens_at=_utc(msk_midnight(start)),
        ends_at=_utc(msk_midnight(end) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=TOPIC_KIND_WEEK,
        created_by_id=owner.id,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _task(db, owner, *, title="Декомпозиции архитектуры", due_on=TODAY, required=True):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        due_at=day_bounds(due_on)[0] + timedelta(hours=6),
        assign_to_all=True, is_required=required,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _block(db, task, *, title="Ролик", order=1, required=False, tariff=None):
    block = TaskBlock(
        task_id=task.id, block_type=BLOCK_TEXT, title=title, body="текст",
        sort_order=order, is_required=required,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    if tariff:
        db.add(TaskBlockTariff(block_id=block.id, tariff=tariff))
        db.commit()
    return block


def _student(user_factory, vk_id, name, tariff="Я С ВАМИ"):
    return user_factory(vk_id=vk_id, name=name, tariff=tariff)


def _ids(debtors):
    return [debtor["user"].id for debtor in debtors]


# ── кто должник ─────────────────────────────────────────────────────────────

def test_debtor_listed_with_missing_task_and_closer_is_not(db, regular_user, user_factory):
    topic = _cycle(db, regular_user)
    task = _task(db, regular_user)
    closer = _student(user_factory, 771_001, "Закрыл")
    close_task_for_user(db, task, closer.id, source="manual")
    db.commit()

    debtors = cycle_debtors(db, topic)

    assert regular_user.id in _ids(debtors)
    assert closer.id not in _ids(debtors)
    mine = next(d for d in debtors if d["user"].id == regular_user.id)
    assert [t.title for t in mine["tasks"]] == ["Декомпозиции архитектуры"]
    assert mine["reminded_today"] is False


def test_blocked_archived_and_service_accounts_are_not_debtors(db, regular_user, user_factory):
    topic = _cycle(db, regular_user)
    _task(db, regular_user)
    blocked = _student(user_factory, 771_002, "Новенький", tariff="")
    blocked.access_until = datetime.now(timezone.utc) - timedelta(hours=1)
    archived = _student(user_factory, 771_003, "Архив")
    archived.archived_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    ids = _ids(cycle_debtors(db, topic))

    assert regular_user.id in ids
    assert blocked.id not in ids
    assert archived.id not in ids


def test_access_until_in_future_keeps_student_in_list(db, regular_user):
    topic = _cycle(db, regular_user)
    _task(db, regular_user)
    regular_user.access_until = datetime.now(timezone.utc) + timedelta(days=2)
    db.commit()

    assert regular_user.id in _ids(cycle_debtors(db, topic))


def test_student_who_came_after_the_cycle_is_not_a_debtor(db, regular_user, user_factory):
    ended = _cycle(db, regular_user, start=TODAY - timedelta(days=10),
                   end=TODAY - timedelta(days=3))
    _task(db, regular_user, due_on=TODAY - timedelta(days=5))
    newcomer = _student(user_factory, 771_004, "Пришёл сегодня")
    newcomer.program_access_from = datetime.now(timezone.utc)
    db.commit()

    ids = _ids(cycle_debtors(db, ended))

    assert regular_user.id in ids
    assert newcomer.id not in ids


def test_hidden_tariff_block_does_not_hold_the_cycle(db, regular_user, user_factory):
    """Регресс 30.09.2026: ученик «Я С ВАМИ» закрыл задание, а в нём остался
    незакрытый ролик тарифа «Уверенный максимум», которого он не видит. Лента
    считает цикл пройденным — статистика раньше не считала."""
    topic = _cycle(db, regular_user)
    task = _task(db, regular_user)
    _block(db, task, title="Для всех", order=1)
    _block(db, task, title="Уверенный максимум", order=2, tariff="УВЕРЕННЫЙ МАКСИМУМ")
    student = _student(user_factory, 771_005, "Я с вами")
    close_task_for_user(db, task, student.id, source="manual")
    db.commit()

    assert is_cycle_complete(db, student.id, topic)
    assert student.id not in _ids(cycle_debtors(db, topic))
    assert cycle_stats(db, topic)["finished"] == 1  # только он, regular_user не закрыл


def test_finished_matches_feed_rule(db, regular_user, user_factory):
    topic = _cycle(db, regular_user)
    task = _task(db, regular_user)
    _block(db, task)
    others = [_student(user_factory, 771_010 + i, f"Ученик {i}") for i in range(3)]
    for student in others[:2]:
        close_task_for_user(db, task, student.id, source="manual")
    db.commit()

    stats = cycle_stats(db, topic)
    everyone = [regular_user, *others]

    assert stats["finished"] == sum(1 for s in everyone if is_cycle_complete(db, s.id, topic))
    assert stats["finished"] == 2


def test_empty_cycle_has_no_debtors(db, regular_user):
    topic = _cycle(db, regular_user)

    assert cycle_debtors(db, topic) == []


def test_optional_task_does_not_make_a_debtor(db, regular_user):
    topic = _cycle(db, regular_user)
    _task(db, regular_user, required=False)

    assert cycle_debtors(db, topic) == []


# ── отправка ────────────────────────────────────────────────────────────────

def test_remind_creates_one_notification_per_debtor_once_a_day(db, regular_user, user_factory):
    topic = _cycle(db, regular_user)
    task = _task(db, regular_user)
    second = _student(user_factory, 771_020, "Второй")
    closer = _student(user_factory, 771_021, "Закрыл")
    close_task_for_user(db, task, closer.id, source="manual")
    db.commit()

    ids, skipped = remind_cycle_debtors(db, topic)

    notes = db.query(Notification).filter(Notification.id.in_(ids)).all()
    assert sorted(n.user_id for n in notes) == sorted([regular_user.id, second.id])
    assert skipped == 0
    note = notes[0]
    assert note.title == "Цикл 3 не закрыт"
    assert "«Декомпозиции архитектуры»" in note.text
    assert "Завершить задание" in note.text
    assert db.query(StudentReminder).filter_by(kind=KIND_CYCLE_DEBT).count() == 2
    assert all(d["reminded_today"] for d in cycle_debtors(db, topic))

    again, skipped_again = remind_cycle_debtors(db, topic)

    assert again == []
    assert skipped_again == 2


def test_remind_next_day_sends_again(db, regular_user):
    topic = _cycle(db, regular_user)
    _task(db, regular_user)
    now = datetime.now(timezone.utc)

    first, _ = remind_cycle_debtors(db, topic, now)
    second, _ = remind_cycle_debtors(db, topic, now + timedelta(days=1))

    assert len(first) == 1 and len(second) == 1


def test_untitled_cycle_title_uses_label(db, regular_user):
    topic = _cycle(db, regular_user, title="Портфолио")
    _task(db, regular_user)

    ids, _ = remind_cycle_debtors(db, topic)

    assert db.get(Notification, ids[0]).title == "Цикл «Портфолио» не закрыт"


# ── роуты ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def sent(monkeypatch):
    calls = []

    async def _fake_notify(notification_id):
        calls.append(notification_id)

    monkeypatch.setattr("app.api.cabinet_program.notify", _fake_notify)
    return calls


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


@pytest.mark.parametrize("role_name", ["админ", "суперадмин"])
def test_gp_and_superadmin_can_remind(client, session_factory, user_factory, db,
                                      regular_user, sent, role_name):
    topic = _cycle(db, regular_user)
    _task(db, regular_user)
    staff = user_factory(vk_id=880_001, name="Сотрудник", role_name=role_name)
    _login(client, session_factory, staff)

    resp = client.post(f"/cabinet/staff/program/cycles/{topic.id}/remind")

    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] == 1 and body["skipped"] == 0
    assert len(sent) == 1
    assert db.get(Notification, sent[0]).user_id == regular_user.id


@pytest.mark.parametrize("role_name", ["ученик", "куратор", "модератор"])
def test_others_cannot_remind(client, session_factory, user_factory, db, regular_user,
                              sent, role_name):
    topic = _cycle(db, regular_user)
    _task(db, regular_user)
    other = user_factory(vk_id=880_002, name="Не ГП", role_name=role_name)
    _login(client, session_factory, other)

    resp = client.post(f"/cabinet/staff/program/cycles/{topic.id}/remind")

    assert resp.status_code in (403, 404)
    assert sent == []
    assert db.query(Notification).count() == 0


def test_stats_page_lists_debtor_and_button_for_gp(client, session_factory, user_factory,
                                                   db, regular_user):
    topic = _cycle(db, regular_user)
    _task(db, regular_user)
    regular_user.last_name, regular_user.first_name = "Армеева", "Дарья"
    db.commit()
    gp = user_factory(vk_id=880_003, name="ГП", role_name="админ")
    _login(client, session_factory, gp)

    html = client.get(f"/cabinet/staff/program/cycles/{topic.id}/stats").text

    assert "Не закрыли цикл: 1" in html
    assert "Армеева Дарья" in html
    assert "Напомнить всем (1)" in html
    assert "Цикл 3 не закрыт" in html  # пример сообщения в панели подтверждения


def test_stats_page_has_no_button_for_moderator(client, session_factory, user_factory,
                                                db, regular_user):
    topic = _cycle(db, regular_user)
    _task(db, regular_user)
    moderator = user_factory(vk_id=880_004, name="Модератор", role_name="модератор")
    _login(client, session_factory, moderator)

    resp = client.get(f"/cabinet/staff/program/cycles/{topic.id}/stats")

    assert resp.status_code == 200
    assert "Не закрыли цикл: 1" in resp.text
    assert "Напомнить всем" not in resp.text
