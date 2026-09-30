"""Вперёд нельзя (владелец 30.09.2026: «не пускать вперёд обязательно и
показывать долги»; запрет действует и между этапами).

Прецедент: карусель пускала в любой начавшийся цикл. Должник цикла 2 не мог
закрыть цикл 3 (тот становился архивом) и работал в цикле 4 — долг цикла 3
оставался навсегда.
"""
from datetime import timedelta, timezone

from app.models.learning_topic import TOPIC_KIND_STAGE, TOPIC_KIND_WEEK, LearningTopic
from app.models.task_block import BLOCK_PHOTO, TaskBlock
from app.services.cycle_feed import feed_for_student, task_is_locked_for_user
from app.services.tracker import (
    close_task_for_user, create_task, cycle_debt, effective_cycle, locked_cycle_ids,
)
from app.services.tz import msk_midnight, today_msk

TODAY = today_msk()


def _utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _topic(db, owner, *, title, starts_on, ends_on, kind=TOPIC_KIND_WEEK, parent=None):
    topic = LearningTopic(
        title=title, opens_at=_utc(msk_midnight(starts_on)),
        ends_at=_utc(msk_midnight(ends_on) + timedelta(hours=23, minutes=59)),
        assign_to_all=True, is_published=True, kind=kind, created_by_id=owner.id,
        parent_id=parent.id if parent is not None else None,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


def _task(db, owner, topic, *, title, required=True):
    task = create_task(
        db, title=title, user_id=owner.id, kind="material",
        topic_id=topic.id, assign_to_all=True, is_required=required,
    )
    task.is_published = True
    db.commit()
    db.refresh(task)
    return task


def _program(db, owner):
    """Этап с тремя циклами, как «Предобучение» 30.09.2026: два закончились,
    третий идёт."""
    stage = _topic(db, owner, title="Предобучение", kind=TOPIC_KIND_STAGE,
                   starts_on=TODAY - timedelta(days=12), ends_on=TODAY + timedelta(days=5))
    c2 = _topic(db, owner, title="Цикл 2", parent=stage,
                starts_on=TODAY - timedelta(days=8), ends_on=TODAY - timedelta(days=3))
    c3 = _topic(db, owner, title="Цикл 3", parent=stage,
                starts_on=TODAY - timedelta(days=5), ends_on=TODAY - timedelta(days=2))
    c4 = _topic(db, owner, title="Цикл 4", parent=stage,
                starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=5))
    t2 = _task(db, owner, c2, title="Формообразование узлов")
    t3 = _task(db, owner, c3, title="Декомпозиции архитектуры")
    t4 = _task(db, owner, c4, title="Подготовка к контрольной")
    return stage, (c2, c3, c4), (t2, t3, t4)


def test_debt_in_cycle_2_locks_3_and_4(db, regular_user):
    _, (c2, c3, c4), _ = _program(db, regular_user)

    debt = cycle_debt(db, regular_user.id, TODAY)

    assert debt["cycle"].id == c2.id
    assert [t.title for t in debt["tasks"]] == ["Формообразование узлов"]
    assert [c.id for c in debt["locked"]] == [c3.id, c4.id]


def test_closing_the_debt_opens_the_next_cycle(db, regular_user):
    _, (c2, c3, c4), (t2, _, _) = _program(db, regular_user)
    close_task_for_user(db, t2, regular_user.id, source="manual")
    db.commit()

    debt = cycle_debt(db, regular_user.id, TODAY)

    assert debt["cycle"].id == c3.id
    assert [c.id for c in debt["locked"]] == [c4.id]


def test_no_debt_nothing_locked(db, regular_user):
    _, _, (t2, t3, _) = _program(db, regular_user)
    for task in (t2, t3):
        close_task_for_user(db, task, regular_user.id, source="manual")
    db.commit()

    assert cycle_debt(db, regular_user.id, TODAY) is None
    assert locked_cycle_ids(db, regular_user.id, TODAY) == set()


def test_optional_task_is_not_a_debt(db, regular_user):
    stage = _topic(db, regular_user, title="Этап", kind=TOPIC_KIND_STAGE,
                   starts_on=TODAY - timedelta(days=10), ends_on=TODAY + timedelta(days=5))
    c1 = _topic(db, regular_user, title="Цикл 1", parent=stage,
                starts_on=TODAY - timedelta(days=8), ends_on=TODAY - timedelta(days=3))
    _topic(db, regular_user, title="Цикл 2", parent=stage,
           starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=5))
    _task(db, regular_user, c1, title="По желанию", required=False)

    assert cycle_debt(db, regular_user.id, TODAY) is None


def test_debt_in_old_stage_locks_new_stage(db, regular_user):
    _, (c2, c3, c4), (t2, t3, t4) = _program(db, regular_user)
    for task in (t2, t3):
        close_task_for_user(db, task, regular_user.id, source="manual")
    october = _topic(db, regular_user, title="Октябрь", kind=TOPIC_KIND_STAGE,
                     starts_on=TODAY, ends_on=TODAY + timedelta(days=30))
    oct1 = _topic(db, regular_user, title="Цикл 1", parent=october,
                  starts_on=TODAY, ends_on=TODAY + timedelta(days=6))
    db.commit()

    debt = cycle_debt(db, regular_user.id, TODAY)

    assert debt["cycle"].id == c4.id
    assert oct1.id in {c.id for c in debt["locked"]}
    feed = feed_for_student(db, user_id=regular_user.id, user_tariff=regular_user.tariff,
                            today=TODAY, cycle_id=oct1.id)
    assert feed["topic"].id == c4.id
    assert feed["debt"]["next"] == "Октябрь: Цикл 1"


def test_feed_shows_debt_and_locked_chips(db, regular_user):
    _, (c2, c3, c4), _ = _program(db, regular_user)

    feed = feed_for_student(db, user_id=regular_user.id, user_tariff=regular_user.tariff,
                            today=TODAY)

    assert feed["topic"].id == c2.id
    assert feed["debt"]["label"] == "Цикл 2"
    assert feed["debt"]["tasks"] == ["Формообразование узлов"]
    assert feed["debt"]["next"] == "Цикл 3"
    chips = {chip["id"]: chip for chip in feed["cycles"]}
    assert chips[c2.id]["is_locked"] is False
    assert chips[c3.id]["is_locked"] is True
    assert chips[c4.id]["is_locked"] is True


def test_locked_cycle_id_opens_the_debt_cycle(db, regular_user):
    _, (c2, _, c4), _ = _program(db, regular_user)

    feed = feed_for_student(db, user_id=regular_user.id, user_tariff=regular_user.tariff,
                            today=TODAY, cycle_id=c4.id)

    assert feed["topic"].id == c2.id


def test_staff_preview_is_not_locked(db, user_factory, regular_user):
    _program(db, regular_user)
    gp = user_factory(vk_id=880_101, name="ГП", role_name="админ")

    assert cycle_debt(db, gp.id, TODAY) is None


def test_locked_task_routes_answer_403(auth_client, db):
    client, student = auth_client
    _, _, (_, _, t4) = _program(db, student)
    block = TaskBlock(task_id=t4.id, block_type=BLOCK_PHOTO, title="Фото", body="",
                      sort_order=1, is_required=False)
    db.add(block)
    db.commit()
    assert task_is_locked_for_user(db, student.id, t4, TODAY)

    blocks = client.get(f"/cabinet/tracker/tasks/{t4.id}/blocks")
    circle = client.post(f"/cabinet/tracker/blocks/{block.id}/done")
    toggle = client.post(f"/cabinet/tracker/tasks/{t4.id}/toggle")

    assert blocks.status_code == 403
    assert circle.status_code == 403
    assert toggle.status_code == 403


def test_debt_cycle_task_is_writable(auth_client, db):
    client, student = auth_client
    _, _, (t2, _, _) = _program(db, student)

    resp = client.post(f"/cabinet/tracker/tasks/{t2.id}/toggle")

    assert resp.status_code == 200
    assert effective_cycle(db, student.id, TODAY).title == "Цикл 3"


def test_learning_page_renders_debt_banner(auth_client, db):
    client, student = auth_client
    _program(db, student)

    html = client.get("/cabinet/learning").text

    assert "Сначала закрой «Цикл 2»" in html
    assert "«Формообразование узлов»" in html
    assert 'class="lrn-cycle-chip is-locked"' in html
