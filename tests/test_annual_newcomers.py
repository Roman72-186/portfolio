"""Новички годового курса: тег «Новенькие» и циклы по тегам.

Школа 24.10.2026: 25.10 в годовой курс заходят новички, им открыт только
цикл адаптации, а недели 1–4 октября скрыты — иначе новичок с первого дня
должник по 1-й неделе и до адаптации не доходит. Решение владельца: у цикла
годового курса «только ученикам с тегами» и «скрыть от учеников с тегами»,
тег «Новенькие» новичок получает сам при заведении
(`user_management.tag_annual_newcomer`).
"""
import asyncio
from datetime import timedelta
from itertools import count

import pytest

import app.api.auth as auth_module
from app.constants import ANNUAL_NEWCOMER_TAG, PROGRAM_HOLIDAY
from app.models.learning_topic import LearningTopic
from app.models.tag import Tag, UserTag
from app.models.user import User
from app.services.cycle_stats import cycle_debtors
from app.services.tags import add_tag_to_user, get_or_create_tag
from app.services.tracker import cycle_debt, effective_cycle
from app.services.tz import today_msk
from app.services.video_topics import (
    accessible_topic_ids,
    get_excluded_tag_ids,
    get_tag_ids,
    set_topic_excluded_tags,
    set_topic_tags,
    topic_audience_user_ids,
)

from tests.test_holiday_intake import (
    CHAT_ID, _callback, _enable_telegram_login, _group_link, _mock_callback,
)
from tests.test_program_access_from import _topic, _undated_task

TODAY = today_msk()
CYCLES_PAGE = "/cabinet/staff/program/cycles"


def _newcomer_tag(db):
    tag = get_or_create_tag(db, ANNUAL_NEWCOMER_TAG)
    db.commit()
    return tag


def _october(db, owner):
    """Прод 24.10.2026 в миниатюре: идущая неделя с замком и адаптация,
    начавшаяся позже. Недели скрыты от «Новеньких», адаптация — только им."""
    tag = _newcomer_tag(db)
    week = _topic(
        db, owner, title="1 неделя",
        starts_on=TODAY - timedelta(days=10), ends_on=TODAY + timedelta(days=7),
    )
    _undated_task(db, owner, week, title="Задание недели")
    adaptation = _topic(
        db, owner, title="октябрь_адаптация",
        starts_on=TODAY - timedelta(days=1), ends_on=TODAY + timedelta(days=7),
    )
    _undated_task(db, owner, adaptation, title="Задание адаптации")
    set_topic_excluded_tags(db, week, [tag.id])
    set_topic_tags(db, adaptation, [tag.id])
    adaptation.assign_to_all = False
    db.commit()
    return tag, week, adaptation


_VK_IDS = count(930_001)


def _student(user_factory, tariff="Я С ВАМИ"):
    return user_factory(vk_id=next(_VK_IDS), tariff=tariff)


@pytest.fixture()
def superadmin_client(client, user_factory, session_factory):
    user = user_factory(vk_id=930_000, name="Super Admin", role_name="суперадмин")
    client.cookies.set("session_id", session_factory(user).id)
    return client, user


# ── видимость ───────────────────────────────────────────────────────────────

def test_newcomer_sees_only_adaptation_old_student_only_week(db, regular_user, user_factory):
    tag, week, adaptation = _october(db, regular_user)
    old = _student(user_factory)
    new = _student(user_factory)
    add_tag_to_user(db, new.id, tag.id)
    db.commit()

    assert week.id in accessible_topic_ids(db, old.id)
    assert adaptation.id not in accessible_topic_ids(db, old.id)
    assert week.id not in accessible_topic_ids(db, new.id)
    assert adaptation.id in accessible_topic_ids(db, new.id)

    assert topic_audience_user_ids(db, week.id) >= {old.id}
    assert new.id not in topic_audience_user_ids(db, week.id)
    assert topic_audience_user_ids(db, adaptation.id) == {new.id}


def test_excluded_tag_beats_addressing(db, regular_user, user_factory):
    """Тег в обоих списках — цикл не виден."""
    tag, _, adaptation = _october(db, regular_user)
    new = _student(user_factory)
    add_tag_to_user(db, new.id, tag.id)
    set_topic_excluded_tags(db, adaptation, [tag.id])
    db.commit()

    assert adaptation.id not in accessible_topic_ids(db, new.id)
    assert new.id not in topic_audience_user_ids(db, adaptation.id)


def test_newcomer_is_not_a_debtor_of_hidden_week(db, regular_user, user_factory):
    tag, week, adaptation = _october(db, regular_user)
    new = _student(user_factory)
    add_tag_to_user(db, new.id, tag.id)
    old = _student(user_factory)
    db.commit()

    assert effective_cycle(db, new.id, TODAY).id == adaptation.id
    assert cycle_debt(db, new.id, TODAY) is None
    # Старый ученик — должник недели, адаптация ему не видна и не заперта.
    old_debt = cycle_debt(db, old.id, TODAY)
    assert old_debt is None or old_debt["cycle"].id == week.id
    assert adaptation.id not in {c.id for c in (old_debt or {}).get("locked", [])}

    debtor_ids = {row["user"].id for row in cycle_debtors(db, week)}
    assert old.id in debtor_ids
    assert new.id not in debtor_ids


# ── тег при заведении ───────────────────────────────────────────────────────

def _has_newcomer_tag(db, user):
    tag = db.query(Tag).filter(Tag.name == ANNUAL_NEWCOMER_TAG).first()
    return tag is not None and db.get(UserTag, (user.id, tag.id)) is not None


def test_new_annual_student_by_telegram_gets_tag(client, db, monkeypatch):
    _enable_telegram_login(monkeypatch)
    _mock_callback(client, monkeypatch, intake_slug=None, member=True)

    resp = _callback(client)

    assert resp.status_code == 302, resp.text
    user = db.query(User).filter(User.telegram_chat_id == CHAT_ID).one()
    assert _has_newcomer_tag(db, user)


def test_existing_student_login_gets_no_tag(client, db, monkeypatch, user_factory):
    _enable_telegram_login(monkeypatch)
    student = user_factory(vk_id=-961, tariff="Я САМ")
    student.telegram_chat_id = CHAT_ID
    db.commit()
    _mock_callback(client, monkeypatch, intake_slug=None, member=True)

    _callback(client)

    db.refresh(student)
    assert not _has_newcomer_tag(db, student)


def test_holiday_newcomer_by_group_link_gets_no_tag(client, db, monkeypatch):
    _enable_telegram_login(monkeypatch)
    link = _group_link(db)
    _mock_callback(client, monkeypatch, intake_slug=link.slug, member=None)

    _callback(client)

    user = db.query(User).filter(User.telegram_chat_id == CHAT_ID).one()
    assert user.program == PROGRAM_HOLIDAY
    assert not _has_newcomer_tag(db, user)


def test_bot_start_new_student_gets_tag(db, monkeypatch):
    async def fake_send(*_a, **_k):
        return None

    async def fake_member(_chat_id):
        return True

    monkeypatch.setattr(auth_module.telegram_service, "send_message", fake_send)
    monkeypatch.setattr(auth_module.telegram_service, "check_channel_membership", fake_member)

    asyncio.run(auth_module._handle_telegram_new_start(db, chat_id=CHAT_ID, tg_from=None))

    user = db.query(User).filter(User.telegram_chat_id == CHAT_ID).one()
    assert _has_newcomer_tag(db, user)


def test_superadmin_added_student_gets_tag(superadmin_client, db):
    client, _ = superadmin_client
    resp = client.post("/cabinet/superadmin/users/create-student", data={
        "first_name": "Новый", "last_name": "Ученик", "tg_username": "@new_arch",
        "tariff": "Я САМ", "csrf_token": "bypass",
    })

    assert resp.status_code == 200
    student = db.query(User).filter(User.first_name == "Новый").one()
    assert _has_newcomer_tag(db, student)


# ── форма цикла ─────────────────────────────────────────────────────────────

def _cycle_payload(**over):
    data = {
        "title": "октябрь_адаптация",
        "starts_on": TODAY.isoformat(),
        "ends_on": (TODAY + timedelta(days=7)).isoformat(),
        "is_published": True,
    }
    data.update(over)
    return data


def test_annual_cycle_form_saves_both_tag_lists_with_tariffs(admin_client, db):
    client, _ = admin_client
    tag = _newcomer_tag(db)
    other = get_or_create_tag(db, "Другой")
    db.commit()

    resp = client.post(CYCLES_PAGE, json=_cycle_payload(
        tag_ids=[tag.id], excluded_tag_ids=[other.id],
        tariff_access=[{"tariff": "Я САМ", "starts_on": None, "ends_on": None}],
    ))
    assert resp.status_code == 200, resp.text
    cycle = db.get(LearningTopic, resp.json()["cycle_id"])

    assert cycle.assign_to_all is False
    assert get_tag_ids(db, cycle.id) == [tag.id]
    assert get_excluded_tag_ids(db, cycle.id) == [other.id]
    assert cycle.tariff_restricted is True

    # Вкладка до выкатки полей не шлёт — адресация остаётся.
    assert client.post(f"{CYCLES_PAGE}/{cycle.id}", json=_cycle_payload()).status_code == 200
    db.expire_all()
    assert get_tag_ids(db, cycle.id) == [tag.id]
    assert get_excluded_tag_ids(db, cycle.id) == [other.id]

    # Пустые списки — снова для всех.
    resp = client.post(f"{CYCLES_PAGE}/{cycle.id}", json=_cycle_payload(tag_ids=[], excluded_tag_ids=[]))
    assert resp.status_code == 200
    db.expire_all()
    assert db.get(LearningTopic, cycle.id).assign_to_all is True
    assert get_excluded_tag_ids(db, cycle.id) == []

    page = client.get(CYCLES_PAGE)
    assert page.status_code == 200
    assert "Скрыть от учеников с тегами" in page.text
    assert "Только ученикам с тегами" in page.text
