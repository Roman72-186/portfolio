"""Доступ к циклу по тарифам с окном «с – по».

Владелец 06.10.2026: «нужно настроить доступность циклов для тарифов и указать,
с какой даты по какое доступен ему данный цикл». Решения владельца: даты
тарифа — окно доступа (цикл идёт в свои общие даты); до «с» цикла у тарифа
нет; после «по» цикл у тарифа в архиве — смотреть можно, делать нельзя.
"""
from datetime import timedelta

from app.models.learning_topic import TOPIC_KIND_WEEK, LearningTopic, LearningTopicTariff
from app.services.cycle_feed import (
    archive_cycle_ids,
    cycle_is_archived_for_user,
    feed_for_student,
)
from app.services.cycle_stats import cycle_debtors, cycle_stats
from app.services.tracker import cycle_debt, effective_cycle
from app.services.tz import msk_midnight, today_msk
from app.services.video_topics import (
    accessible_topic_ids,
    set_topic_tariff_windows,
    topic_audience_user_ids,
)

from tests.test_program_access_from import _topic, _undated_task, _utc

TODAY = today_msk()
CYCLES_PAGE = "/cabinet/staff/program/cycles"


def _day_start(day):
    return _utc(msk_midnight(day))


def _day_end(day):
    return _utc(msk_midnight(day) + timedelta(hours=23, minutes=59, seconds=59))


def _student(db, user, tariff):
    user.tariff = tariff
    db.commit()
    return user


def _running_cycle(db, owner, title="Цикл"):
    cycle = _topic(
        db, owner, title=title,
        starts_on=TODAY - timedelta(days=3), ends_on=TODAY + timedelta(days=10),
    )
    _undated_task(db, owner, cycle, title=f"Задание: {title}")
    return cycle


# ── видимость ───────────────────────────────────────────────────────────────

def test_cycle_without_tariff_rule_is_open_to_everyone(db, regular_user):
    _student(db, regular_user, "Я САМ")
    cycle = _running_cycle(db, regular_user)

    assert cycle.id in accessible_topic_ids(db, regular_user.id)


def test_unchecked_tariff_does_not_see_the_cycle(db, regular_user):
    _student(db, regular_user, "Я САМ")
    cycle = _running_cycle(db, regular_user)
    set_topic_tariff_windows(db, cycle, {"Я С ВАМИ": (None, None)})
    db.commit()

    assert cycle.id not in accessible_topic_ids(db, regular_user.id)


def test_tariff_sees_the_cycle_only_from_its_start_date(db, regular_user):
    _student(db, regular_user, "Я САМ")
    cycle = _running_cycle(db, regular_user)
    set_topic_tariff_windows(
        db, cycle, {"Я САМ": (_day_start(TODAY + timedelta(days=2)), None)}
    )
    db.commit()

    assert cycle.id not in accessible_topic_ids(db, regular_user.id)
    assert regular_user.id not in topic_audience_user_ids(db, cycle.id)

    set_topic_tariff_windows(db, cycle, {"Я САМ": (_day_start(TODAY), None)})
    db.commit()

    assert cycle.id in accessible_topic_ids(db, regular_user.id)
    assert regular_user.id in topic_audience_user_ids(db, cycle.id)


# ── после «по» — архив ──────────────────────────────────────────────────────

def _closed_for_tariff(db, owner):
    """Цикл идёт по общим датам, а окно «Я САМ» закончилось вчера; следом —
    второй цикл, который должен открыться, а не запереться долгом."""
    closed = _running_cycle(db, owner, title="Цикл 1")
    set_topic_tariff_windows(db, closed, {
        "Я САМ": (None, _day_end(TODAY - timedelta(days=1))),
        "Я С ВАМИ": (None, None),
    })
    following = _topic(
        db, owner, title="Цикл 2",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=10),
    )
    _undated_task(db, owner, following, title="Задание: Цикл 2")
    db.commit()
    return closed, following


def test_after_end_date_cycle_goes_to_archive_and_does_not_hold_debt(db, regular_user):
    _student(db, regular_user, "Я САМ")
    closed, following = _closed_for_tariff(db, regular_user)

    # Цикл по-прежнему доступен — архиву он нужен.
    assert closed.id in accessible_topic_ids(db, regular_user.id)
    assert effective_cycle(db, regular_user.id, TODAY).id == following.id
    assert cycle_debt(db, regular_user.id, TODAY) is None
    assert cycle_is_archived_for_user(db, regular_user.id, closed.id, TODAY)
    assert closed.id in archive_cycle_ids(
        db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY,
    )

    feed = feed_for_student(
        db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY, cycle_id=closed.id,
    )
    assert feed["topic"].id == closed.id
    assert feed["is_archive"] is True


def test_other_tariff_keeps_working_in_the_same_cycle(db, regular_user):
    """Окно одного тарифа не трогает другой: у «Я С ВАМИ» цикл идёт, и его
    незакрытое задание держит долгом, как раньше."""
    _student(db, regular_user, "Я С ВАМИ")
    closed, following = _closed_for_tariff(db, regular_user)

    assert effective_cycle(db, regular_user.id, TODAY).id == closed.id
    assert not cycle_is_archived_for_user(db, regular_user.id, closed.id, TODAY)
    debt = cycle_debt(db, regular_user.id, TODAY)
    assert debt is not None and debt["cycle"].id == closed.id
    assert following.id in {c.id for c in debt["locked"]}


def test_stats_count_only_open_tariffs_and_skip_closed_debtors(db, regular_user, user_factory):
    self_student = _student(db, regular_user, "Я САМ")
    with_you = user_factory(vk_id=100_101, name="С вами")
    with_you.tariff = "Я С ВАМИ"
    max_student = user_factory(vk_id=100_102, name="Максимум")
    max_student.tariff = "УВЕРЕННЫЙ МАКСИМУМ"
    db.commit()
    closed, _ = _closed_for_tariff(db, regular_user)

    stats = cycle_stats(db, closed)
    assert stats["students_by_tariff"]["УВЕРЕННЫЙ МАКСИМУМ"] == 0
    assert stats["students_by_tariff"]["Я САМ"] == 1
    assert stats["students_by_tariff"]["Я С ВАМИ"] == 1

    debtor_ids = {row["user"].id for row in cycle_debtors(db, closed)}
    assert with_you.id in debtor_ids
    assert self_student.id not in debtor_ids
    assert max_student.id not in debtor_ids


# ── форма цикла ─────────────────────────────────────────────────────────────

def _cycle_payload(**over):
    data = {
        "title": "Цикл с тарифами",
        "starts_on": TODAY.isoformat(),
        "ends_on": (TODAY + timedelta(days=14)).isoformat(),
        "is_published": True,
    }
    data.update(over)
    return data


def _rows(db, topic_id):
    return {
        row.tariff: row
        for row in db.query(LearningTopicTariff).filter(LearningTopicTariff.topic_id == topic_id)
    }


def test_form_saves_tariff_windows_as_whole_days(admin_client, db):
    client, _ = admin_client
    resp = client.post(CYCLES_PAGE, json=_cycle_payload(tariff_access=[
        {"tariff": "Я САМ", "starts_on": (TODAY + timedelta(days=1)).isoformat(),
         "ends_on": (TODAY + timedelta(days=7)).isoformat()},
        {"tariff": "я с вами", "starts_on": None, "ends_on": None},
    ]))
    assert resp.status_code == 200, resp.text
    cycle = db.get(LearningTopic, resp.json()["cycle_id"])

    assert cycle.tariff_restricted is True
    rows = _rows(db, cycle.id)
    assert set(rows) == {"Я САМ", "Я С ВАМИ"}
    assert _utc(rows["Я САМ"].opens_at) == _day_start(TODAY + timedelta(days=1))
    assert _utc(rows["Я САМ"].closes_at) == _day_end(TODAY + timedelta(days=7))
    assert rows["Я С ВАМИ"].opens_at is None and rows["Я С ВАМИ"].closes_at is None

    page = client.get(CYCLES_PAGE).text
    assert "Доступ по тарифам" in page
    assert f"Я САМ с {(TODAY + timedelta(days=1)).isoformat()}" in page


def test_form_without_the_field_keeps_tariff_access(admin_client, db):
    """Вкладка, открытая до выкатки, поле не присылает — доступ не сносится."""
    client, _ = admin_client
    resp = client.post(CYCLES_PAGE, json=_cycle_payload(tariff_access=[
        {"tariff": "Я САМ", "starts_on": None, "ends_on": None},
    ]))
    cycle_id = resp.json()["cycle_id"]

    assert client.post(f"{CYCLES_PAGE}/{cycle_id}", json=_cycle_payload()).status_code == 200
    db.expire_all()
    assert db.get(LearningTopic, cycle_id).tariff_restricted is True
    assert set(_rows(db, cycle_id)) == {"Я САМ"}

    # Явный null — снова открыт всем.
    resp = client.post(f"{CYCLES_PAGE}/{cycle_id}", json=_cycle_payload(tariff_access=None))
    assert resp.status_code == 200
    db.expire_all()
    assert db.get(LearningTopic, cycle_id).tariff_restricted is False
    assert _rows(db, cycle_id) == {}


def test_form_rejects_end_before_start_unknown_tariff_and_duplicates(admin_client, db):
    client, _ = admin_client
    bad = [
        [{"tariff": "Я САМ", "starts_on": (TODAY + timedelta(days=5)).isoformat(),
          "ends_on": TODAY.isoformat()}],
        [{"tariff": "ПЛАТИНА"}],
        [{"tariff": "Я САМ"}, {"tariff": "я сам"}],
    ]
    for access in bad:
        resp = client.post(CYCLES_PAGE, json=_cycle_payload(tariff_access=access))
        assert resp.status_code == 422, (access, resp.text)
    assert db.query(LearningTopic).filter(LearningTopic.kind == TOPIC_KIND_WEEK).count() == 0


def test_student_side_sees_cycle_only_in_window(db, regular_user):
    """Связка с лентой: до «с» у тарифа цикла нет и в ленте."""
    _student(db, regular_user, "Я САМ")
    cycle = _running_cycle(db, regular_user)
    set_topic_tariff_windows(
        db, cycle, {"Я САМ": (_day_start(TODAY + timedelta(days=1)), None)}
    )
    db.commit()

    feed = feed_for_student(db, user_id=regular_user.id, user_tariff="Я САМ", today=TODAY)
    assert feed["topic"] is None or feed["topic"].id != cycle.id
    assert cycle.id not in {c["id"] for c in feed["cycles"]}
