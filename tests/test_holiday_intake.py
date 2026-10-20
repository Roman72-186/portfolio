"""Каникулярный набор, вход: ссылки групп `/join/<slug>` и вход без канала.

Созвон 13.10.2026, план `plans/2026-10-14-apparchi-созвон-13-10.md`, часть 3.2.
Правила — `app/services/intake_link.py`: новичок по ссылке группы получает
каникулярную программу, тег группы и срок и в закрытый канал не проверяется;
уже заведённого человека судит его программа, а не ссылка. Разделение самих
программ — `tests/test_holiday_program.py`, ссылка `/proba` —
`tests/test_intake_link.py`.
"""
import asyncio
from datetime import datetime, timezone

import app.api.auth as auth_module
from app.constants import INTAKE_TRIAL_SLUG, PROGRAM_ANNUAL, PROGRAM_HOLIDAY
from app.models.intake_link import IntakeLink
from app.models.session import Session
from app.models.tag import Tag, UserTag
from app.models.user import User

CHAT_ID = 700_300_400
DEADLINE = datetime(2026, 11, 1, 20, 59, tzinfo=timezone.utc)  # 01.11 23:59 МСК


def _enable_telegram_login(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "telegram_login_client_id", "123456")
    monkeypatch.setattr(settings, "telegram_login_client_secret", "secret")


def _group_link(db, *, is_active=True, access_until=DEADLINE, slug="grp2345a", tag_name="Каникулы 1"):
    tag = Tag(name=tag_name)
    db.add(tag)
    db.flush()
    link = IntakeLink(
        slug=slug, title=tag_name, program=PROGRAM_HOLIDAY, tag_id=tag.id,
        is_active=is_active, access_until=access_until,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def _mock_callback(client, monkeypatch, *, intake_slug, member):
    """Вход через Telegram до проверки канала. `member` — что ответил бы канал;
    `None` — проверка вызываться не должна вовсе."""
    calls = []
    extra = {"purpose": auth_module.TG_PURPOSE_LOGIN}
    if intake_slug:
        extra["intake_slug"] = intake_slug

    async def fake_exchange_code(*_a, **_k):
        return {"id_token": "fake.jwt.token"}

    def fake_verify_id_token(_id_token):
        return {"id": CHAT_ID, "preferred_username": "kanikuly", "given_name": "Аня", "family_name": "К"}

    async def fake_check_membership(chat_id):
        calls.append(chat_id)
        assert member is not None, "каникулярного не проверяют в канале"
        return member

    monkeypatch.setattr(auth_module, "pop_telegram_oidc_pkce", lambda s: (
        {"code_verifier": "v", **extra} if s == "redis-state" else None
    ))
    monkeypatch.setattr(auth_module, "tg_exchange_code", fake_exchange_code)
    monkeypatch.setattr(auth_module, "tg_verify_id_token", fake_verify_id_token)
    monkeypatch.setattr(auth_module.telegram_service, "check_channel_membership", fake_check_membership)
    client.cookies.set("tg_pkce_cv", auth_module._signer.dumps({"cv": "cookie-verifier", "st": "redis-state"}))
    return calls


def _callback(client):
    return client.get("/auth/telegram-login/callback?code=good&state=redis-state", follow_redirects=False)


# ---------------------------------------------------------------------------
# GET /join/{slug}
# ---------------------------------------------------------------------------

def test_join_unknown_or_trial_slug_is_404(client, db, monkeypatch):
    _enable_telegram_login(monkeypatch)
    db.add(IntakeLink(slug=INTAKE_TRIAL_SLUG, title="Пробный", is_active=True, access_until=DEADLINE))
    db.commit()

    assert client.get("/join/nosuch12", follow_redirects=False).status_code == 404
    # `/proba` — годовая программа, через `/join/` её не открыть.
    assert client.get(f"/join/{INTAKE_TRIAL_SLUG}", follow_redirects=False).status_code == 404


def test_join_closed_link_says_registration_closed_without_login(client, db, monkeypatch):
    _enable_telegram_login(monkeypatch)
    link = _group_link(db, is_active=False)

    resp = client.get(f"/join/{link.slug}", follow_redirects=False)

    assert resp.status_code == 200  # открытая ссылка уводит в Telegram редиректом


def test_join_open_link_carries_its_slug_to_telegram(client, db, monkeypatch):
    _enable_telegram_login(monkeypatch)
    link = _group_link(db)
    captured = {}

    def fake_set_pkce(state, code_verifier, ttl=300, extra=None):
        captured.update(extra or {})
        return True

    monkeypatch.setattr(auth_module, "set_telegram_oidc_pkce", fake_set_pkce)

    resp = client.get(f"/join/{link.slug}", follow_redirects=False)

    assert resp.status_code == 302
    assert "oauth.telegram.org" in resp.headers["location"]
    assert captured["intake_slug"] == link.slug
    cookie = auth_module._signer.loads(resp.cookies.get("tg_pkce_cv"), max_age=600)
    assert cookie["intake_slug"] == link.slug


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------

def test_new_user_by_group_link_gets_program_tag_deadline_without_channel(client, db, monkeypatch):
    link = _group_link(db)
    calls = _mock_callback(client, monkeypatch, intake_slug=link.slug, member=None)

    resp = _callback(client)

    assert resp.status_code == 302 and resp.headers["location"] == "/cabinet"
    assert calls == []
    user = db.query(User).filter(User.telegram_chat_id == CHAT_ID).one()
    assert user.program == PROGRAM_HOLIDAY
    assert user.is_group_member is True
    assert user.tariff == ""
    assert user.access_until == DEADLINE
    assert db.get(UserTag, (user.id, link.tag_id)) is not None


def test_login_writes_ip_and_browser_into_session(client, db, monkeypatch):
    link = _group_link(db)
    _mock_callback(client, monkeypatch, intake_slug=link.slug, member=None)

    resp = client.get(
        "/auth/telegram-login/callback?code=good&state=redis-state",
        headers={"User-Agent": "Mozilla/5.0 (iPhone)"}, follow_redirects=False,
    )

    session = db.get(Session, resp.cookies.get("session_id"))
    assert session.ip == "testclient"
    assert session.user_agent == "Mozilla/5.0 (iPhone)"


def test_holiday_student_logs_in_again_without_link_and_channel(client, db, monkeypatch, user_factory):
    student = user_factory(vk_id=-950, tariff="")
    student.telegram_chat_id = CHAT_ID
    student.program = PROGRAM_HOLIDAY
    db.commit()
    calls = _mock_callback(client, monkeypatch, intake_slug=None, member=None)

    resp = _callback(client)

    assert resp.status_code == 302 and resp.headers["location"] == "/cabinet"
    assert calls == []
    db.refresh(student)
    assert student.is_group_member is True


def test_annual_student_by_group_link_stays_annual(client, db, monkeypatch, user_factory):
    """Ссылка действует только на новичка (правило `/proba`): годовой ученик
    проходит обычную проверку канала и ни программы, ни тега, ни срока не получает."""
    link = _group_link(db)
    student = user_factory(vk_id=-951, tariff="УВЕРЕННЫЙ")
    student.telegram_chat_id = CHAT_ID
    db.commit()
    calls = _mock_callback(client, monkeypatch, intake_slug=link.slug, member=True)

    resp = _callback(client)

    assert resp.status_code == 302
    assert calls == [CHAT_ID]
    db.refresh(student)
    assert student.program == PROGRAM_ANNUAL
    assert student.tariff == "УВЕРЕННЫЙ"
    assert student.access_until is None
    assert db.get(UserTag, (student.id, link.tag_id)) is None


def test_annual_student_out_of_channel_is_refused_even_by_group_link(client, db, monkeypatch, user_factory):
    link = _group_link(db)
    student = user_factory(vk_id=-952)
    student.telegram_chat_id = CHAT_ID
    db.commit()
    _mock_callback(client, monkeypatch, intake_slug=link.slug, member=False)

    resp = _callback(client)

    assert resp.status_code == 200 and "session_id" not in resp.cookies
    db.refresh(student)
    assert student.is_group_member is False


def test_group_link_closed_during_login_lets_nobody_new_in(client, db, monkeypatch):
    link = _group_link(db)
    _mock_callback(client, monkeypatch, intake_slug=link.slug, member=None)
    link.is_active = False
    db.commit()

    resp = _callback(client)

    assert resp.status_code == 200
    assert db.query(User).filter(User.telegram_chat_id == CHAT_ID).first() is None


def test_regular_login_still_checks_channel_for_newcomer(client, db, monkeypatch):
    calls = _mock_callback(client, monkeypatch, intake_slug=None, member=False)

    resp = _callback(client)

    assert calls == [CHAT_ID]
    assert "session_id" not in resp.cookies
    assert db.query(User).filter(User.telegram_chat_id == CHAT_ID).first() is None


# ---------------------------------------------------------------------------
# Бот /start
# ---------------------------------------------------------------------------

def test_bot_start_keeps_holiday_student_without_channel(db, monkeypatch, user_factory):
    student = user_factory(vk_id=-953, tariff="")
    student.telegram_chat_id = CHAT_ID
    student.program = PROGRAM_HOLIDAY
    db.commit()
    sent = []

    async def fake_send(chat_id, text, **_kw):
        sent.append(text)

    async def fake_check_membership(_chat_id):
        raise AssertionError("каникулярного не проверяют в канале")

    monkeypatch.setattr(auth_module.telegram_service, "send_message", fake_send)
    monkeypatch.setattr(auth_module.telegram_service, "check_channel_membership", fake_check_membership)

    asyncio.run(auth_module._handle_telegram_new_start(db, chat_id=CHAT_ID, tg_from=None))

    db.refresh(student)
    assert student.is_group_member is True
    assert sent and "Telegram подключён" in sent[0]


# ---------------------------------------------------------------------------
# /cabinet/periods: ссылки групп
# ---------------------------------------------------------------------------

def test_create_group_link_makes_tag_and_closed_link(admin_client, db):
    client, _admin = admin_client

    resp = client.post(
        "/cabinet/intake/links",
        data={"csrf_token": "x", "title": "Каникулы 2", "deadline": "2026-11-01T23:59"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    link = db.query(IntakeLink).filter(IntakeLink.program == PROGRAM_HOLIDAY).one()
    tag = db.get(Tag, link.tag_id)
    assert tag.name == "Каникулы 2" and tag.is_hidden is False
    assert link.is_active is False
    assert link.access_until == DEADLINE

    assert client.get("/cabinet/periods").status_code == 200


def test_group_link_reuses_existing_tag(admin_client, db):
    client, _admin = admin_client
    existing = Tag(name="Каникулы 3")
    db.add(existing)
    db.commit()

    client.post("/cabinet/intake/links", data={"csrf_token": "x", "title": "каникулы 3"})

    link = db.query(IntakeLink).filter(IntakeLink.program == PROGRAM_HOLIDAY).one()
    assert link.tag_id == existing.id


def test_group_link_needs_a_name(admin_client, db):
    client, _admin = admin_client
    assert client.post("/cabinet/intake/links", data={"csrf_token": "x", "title": " "}).status_code == 400
    assert client.post(
        "/cabinet/intake/links", data={"csrf_token": "x", "title": "я" * 51},
    ).status_code == 400
    assert db.query(IntakeLink).count() == 0


def test_group_link_toggle_and_deadline_by_slug(admin_client, db):
    client, _admin = admin_client
    link = _group_link(db, is_active=False, access_until=None)

    assert client.post(f"/cabinet/intake/{link.slug}/toggle", data={"csrf_token": "x"}).status_code == 400

    client.post(f"/cabinet/intake/{link.slug}/deadline", data={"csrf_token": "x", "deadline": "2026-11-01T23:59"})
    resp = client.post(f"/cabinet/intake/{link.slug}/toggle", data={"csrf_token": "x"}, follow_redirects=False)

    assert resp.status_code == 303
    db.refresh(link)
    assert link.is_active is True and link.access_until == DEADLINE
    # `/proba` от этого не заводится и не включается.
    assert db.query(IntakeLink).filter(IntakeLink.slug == INTAKE_TRIAL_SLUG).first() is None


def test_unknown_slug_is_not_created_by_toggle(admin_client, db):
    client, _admin = admin_client
    assert client.post("/cabinet/intake/nosuch12/toggle", data={"csrf_token": "x"}).status_code == 404
    assert db.query(IntakeLink).count() == 0


def test_student_and_curator_cannot_create_group_link(auth_client, client, db, session_factory, user_factory):
    student_client, _student = auth_client
    assert student_client.post("/cabinet/intake/links", data={"csrf_token": "x", "title": "Г"}).status_code == 403

    curator = user_factory(vk_id=-954, role_name="куратор")
    client.cookies.set("session_id", session_factory(curator).id)
    assert client.post("/cabinet/intake/links", data={"csrf_token": "x", "title": "Г"}).status_code == 403
    assert db.query(IntakeLink).count() == 0
