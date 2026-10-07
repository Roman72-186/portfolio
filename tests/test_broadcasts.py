"""Рассылки ученикам через бота (владелец 07.10.2026) — `services/broadcasts.py`,
`services/broadcast_delivery.py`, экран `api/cabinet_broadcasts.py`.

Telegram подменяется фейком `FakeTelegram`: он запоминает вызовы и отвечает
как Bot API, включая `file_id` загруженного файла.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.constants import REPORT_EXCLUDED_USER_IDS
from app.models.broadcast import (
    RECIPIENT_FAILED,
    RECIPIENT_NO_TELEGRAM,
    RECIPIENT_NOTIFICATIONS_OFF,
    RECIPIENT_SENT,
    STATUS_DRAFT,
    STATUS_SENDING,
    STATUS_SENT,
    Broadcast,
    BroadcastRecipient,
)
from app.services import broadcast_delivery as delivery
from app.services import broadcasts as bc
from app.services import telegram as telegram_service
from app.services.task_blocks import BlockAudience

ADMIN_CHAT = 500_000


class FakeTelegram:
    def __init__(self):
        self.calls: list[tuple[str, dict, dict | None]] = []
        self.fail_chats: dict[int, telegram_service.ApiResult] = {}
        self.queue: list[telegram_service.ApiResult] = []
        self.next_message_id = 1

    async def __call__(self, method, data, *, files=None, timeout=60.0):
        self.calls.append((method, dict(data), files))
        if self.queue:
            return self.queue.pop(0)
        chat = data.get("chat_id")
        if chat in self.fail_chats:
            return self.fail_chats[chat]
        self.next_message_id += 1
        result = {"message_id": self.next_message_id}
        if method == "sendPhoto":
            result["photo"] = [{"file_id": "small"}, {"file_id": "PHOTO-ID"}]
        elif method == "sendVoice":
            result["voice"] = {"file_id": "VOICE-ID"}
        elif method == "sendVideoNote":
            result["video_note"] = {"file_id": "NOTE-ID"}
        return telegram_service.ApiResult(ok=True, status=200, result=result)

    def to(self, chat_id):
        return [(m, d, f) for m, d, f in self.calls if d.get("chat_id") == chat_id]


@pytest.fixture()
def fake_tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(delivery.telegram_service, "call_api", fake)

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(delivery.asyncio, "sleep", _no_sleep)
    return fake


@pytest.fixture()
def s3_store(monkeypatch):
    store: dict[str, bytes] = {}

    def upload(path, data, content_type="image/jpeg"):
        store[path] = data
        return f"https://s3.test/{path}"

    monkeypatch.setattr(bc_api_s3(), "upload_to_s3", upload)
    monkeypatch.setattr(bc_api_s3(), "download_from_s3", lambda path: store.get(path))
    monkeypatch.setattr(bc_api_s3(), "delete_from_s3", lambda path: store.pop(path, None) is not None)
    return store


def bc_api_s3():
    from app.services import s3
    return s3


@pytest.fixture()
def no_transcode(monkeypatch):
    from app.services import media_transcode
    monkeypatch.setattr(media_transcode, "telegram_voice", lambda n, d, c: ("voice.ogg", d, "audio/ogg"))
    monkeypatch.setattr(media_transcode, "telegram_note", lambda n, d, c: ("note.mp4", d, "video/mp4"))


_vk = iter(range(700_000, 799_999))


@pytest.fixture()
def student(user_factory, db):
    def _make(*, tariff="Я С ВАМИ", chat=None, notifications=True, **extra):
        user = user_factory(vk_id=next(_vk), name="Ученик", tariff=tariff)
        user.first_name = extra.pop("first_name", "Имя")
        user.last_name = extra.pop("last_name", f"Фамилия{user.id}")
        user.telegram_chat_id = chat
        user.telegram_notifications_enabled = notifications
        for key, value in extra.items():
            setattr(user, key, value)
        db.commit()
        return user
    return _make


@pytest.fixture()
def gp_client(client, session_factory, user_factory, db):
    gp = user_factory(vk_id=880_001, name="ГП", role_name="админ")
    gp.telegram_chat_id = ADMIN_CHAT
    db.commit()
    sess = session_factory(gp)
    client.cookies.set("session_id", sess.id)
    return client, gp


# ── Разметка ────────────────────────────────────────────────────────────────


def test_clean_maps_editor_html_to_telegram_tags():
    raw = (
        '<div><strong>Жирно</strong>, <em>курсив</em>, <u>под</u>, <strike>зач</strike></div>'
        '<div>Скрыто: <span class="tg-spoiler">тайна</span></div>'
        '<blockquote>цитата</blockquote>'
    )
    assert bc.clean_telegram_html(raw) == (
        "<b>Жирно</b>, <i>курсив</i>, <u>под</u>, <s>зач</s>\n"
        "Скрыто: <tg-spoiler>тайна</tg-spoiler>\n"
        "<blockquote>цитата</blockquote>"
    )


def test_clean_drops_unsafe_links_scripts_and_attributes():
    raw = (
        '<a href="javascript:alert(1)">плохо</a> <a href="https://apparchi.ru" onclick="x()">хорошо</a>'
        '<script>alert(1)</script><span style="color:red">текст</span> 1 < 2 & 3'
    )
    assert bc.clean_telegram_html(raw) == (
        'плохо <a href="https://apparchi.ru">хорошо</a>текст 1 &lt; 2 &amp; 3'
    )


def test_clean_is_idempotent_and_drops_empty_tags():
    once = bc.clean_telegram_html("<b></b><p>Первая</p><p>Вторая<br>строка</p><i> </i>")
    assert once == "Первая\nВторая\nстрока"
    assert bc.clean_telegram_html(once) == once
    assert bc.clean_telegram_html(bc.telegram_html_for_page(
        "<b>a</b>\n<tg-spoiler>b</tg-spoiler>"
    )) == "<b>a</b>\n<tg-spoiler>b</tg-spoiler>"


def test_clean_closes_overlapping_tags_in_order():
    assert bc.clean_telegram_html("<b><i>x</b>y</i>") == "<b><i>x</i></b>y"


def test_visible_length_counts_like_telegram():
    assert bc.visible_length("<b>ab</b> &amp;") == 4
    assert bc.visible_length("😀") == 2  # UTF-16


# ── Получатели ──────────────────────────────────────────────────────────────


def test_audience_by_tariff_level_and_name(db, student, monkeypatch):
    with_you_l2 = student(tariff="Я С ВАМИ", chat=1)
    with_you_l1 = student(tariff="Я С ВАМИ", chat=2)
    self_l2 = student(tariff="Я САМ", chat=3)
    named = student(tariff="Я САМ", chat=4)
    levels = {with_you_l2.id: 2, with_you_l1.id: 1, self_l2.id: 2, named.id: None}
    monkeypatch.setattr(
        "app.services.point_a.student_point_a_level", lambda _db, user: levels.get(user.id),
    )

    def ids(**kw):
        return {u.id for u in bc.audience_users(db, BlockAudience(**kw))}

    assert ids(tariffs=frozenset({"Я С ВАМИ"})) == {with_you_l2.id, with_you_l1.id}
    assert ids(tariffs=frozenset({"Я С ВАМИ"}), levels=frozenset({2})) == {with_you_l2.id}
    assert ids(levels=frozenset({2})) == {with_you_l2.id, self_l2.id}
    assert ids(user_ids=frozenset({named.id})) == {named.id}
    assert ids(tariffs=frozenset({"Я С ВАМИ"}), user_ids=frozenset({named.id})) == {
        with_you_l2.id, with_you_l1.id, named.id,
    }
    assert ids() == set()  # пустой выбор — никому, а не всем


def test_audience_skips_closed_accounts_and_service_by_tariff(db, student, user_factory):
    active = student()
    blocked = student(is_active=False)
    archived = student(archived_at=datetime.now(timezone.utc))
    expired = student(access_until=datetime.now(timezone.utc) - timedelta(days=1))
    service_id = sorted(REPORT_EXCLUDED_USER_IDS)[0]
    service = user_factory(vk_id=next(_vk), tariff="Я С ВАМИ")
    service.id = service_id
    db.commit()
    by_tariff = {u.id for u in bc.audience_users(db, BlockAudience(tariffs=frozenset({"Я С ВАМИ"})))}
    assert by_tariff == {active.id}
    assert blocked.id not in by_tariff and archived.id not in by_tariff and expired.id not in by_tariff
    named = {u.id for u in bc.audience_users(db, BlockAudience(user_ids=frozenset({service_id})))}
    assert named == {service_id}  # служебный — только поимённо, для проверки


def test_summary_counts_unreachable(db, student):
    users = [student(chat=1), student(chat=None), student(chat=3, notifications=False)]
    summary = bc.summarize(users)
    assert (summary.total, summary.reachable, summary.no_telegram, summary.notifications_off) == (3, 1, 1, 1)


# ── Путь на экране ──────────────────────────────────────────────────────────


def _create(client) -> int:
    resp = client.post("/cabinet/staff/broadcasts", follow_redirects=False)
    assert resp.status_code == 302
    return int(resp.headers["location"].rsplit("/", 1)[1])


def _save(client, bid, *, action="save", files=None, **form):
    data = {"text": "<b>Привет</b>", "tariffs": ["Я С ВАМИ"], "action": action}
    data.update(form)
    return client.post(f"/cabinet/staff/broadcasts/{bid}", data=data, files=files, follow_redirects=False)


def test_full_flow_preview_then_send(gp_client, db, student, fake_tg):
    client, gp = gp_client
    reachable = student(chat=101)
    no_bot = student(chat=None)
    muted = student(chat=103, notifications=False)
    student(tariff="Я САМ", chat=104)

    bid = _create(client)
    assert "error" not in _save(client, bid).headers["location"]

    resp = client.post(f"/cabinet/staff/broadcasts/{bid}/send", follow_redirects=False)
    assert "error=" in resp.headers["location"]  # без проверки не отправить
    assert fake_tg.calls == []

    resp = _save(client, bid, action="preview")
    assert resp.headers["location"].endswith("ok=preview")
    to_admin = fake_tg.to(ADMIN_CHAT)
    assert to_admin[0][0] == "sendMessage"
    assert to_admin[0][1]["text"] == "<b>Привет</b>" and to_admin[0][1]["parse_mode"] == "HTML"
    assert "Отправить 1 ученику" in to_admin[1][1]["reply_markup"]
    db.expire_all()
    assert bc.preview_is_current(db, db.get(Broadcast, bid))

    resp = client.post(f"/cabinet/staff/broadcasts/{bid}/send", follow_redirects=False)
    assert resp.headers["location"].endswith("ok=sending")
    db.expire_all()
    broadcast = db.get(Broadcast, bid)
    assert broadcast.status == STATUS_SENT and broadcast.sent_by_id == gp.id
    statuses = {r.user_id: r.status for r in db.query(BroadcastRecipient).filter_by(broadcast_id=bid)}
    assert statuses == {
        reachable.id: RECIPIENT_SENT,
        no_bot.id: RECIPIENT_NO_TELEGRAM,
        muted.id: RECIPIENT_NOTIFICATIONS_OFF,
    }
    assert [m for m, _, _ in fake_tg.to(101)] == ["sendMessage"]
    assert fake_tg.to(103) == [] and fake_tg.to(104) == []

    page = client.get(f"/cabinet/staff/broadcasts/{bid}")
    assert page.status_code == 200 and "Бот не подключён" in page.text
    again = client.post(f"/cabinet/staff/broadcasts/{bid}/send", follow_redirects=False)
    assert "error=" in again.headers["location"]


def test_edit_after_preview_requires_new_preview(gp_client, db, student, fake_tg):
    client, _ = gp_client
    student(chat=101)
    bid = _create(client)
    _save(client, bid, action="preview")
    _save(client, bid, text="<b>Другой текст</b>")
    resp = client.post(f"/cabinet/staff/broadcasts/{bid}/send", follow_redirects=False)
    assert "error=" in resp.headers["location"]
    # Смена одних получателей — тоже новая версия.
    _save(client, bid, text="<b>Другой текст</b>", action="preview")
    _save(client, bid, text="<b>Другой текст</b>", tariffs=["Я САМ"])
    db.expire_all()
    assert not bc.preview_is_current(db, db.get(Broadcast, bid))


def test_voice_goes_to_students_by_file_id(gp_client, db, student, fake_tg, s3_store, no_transcode):
    client, _ = gp_client
    student(chat=101)
    student(chat=102)
    bid = _create(client)
    files = {"audio": ("rec.webm", b"OGGDATA", "audio/webm")}
    assert _save(client, bid, action="preview", files=files).headers["location"].endswith("ok=preview")
    upload = fake_tg.to(ADMIN_CHAT)[0]
    assert upload[0] == "sendVoice" and upload[2]["voice"][1] == b"OGGDATA"
    assert upload[1]["caption"] == "<b>Привет</b>"

    client.post(f"/cabinet/staff/broadcasts/{bid}/send")
    for chat in (101, 102):
        (method, data, files_sent), = fake_tg.to(chat)
        assert method == "sendVoice" and data["voice"] == "VOICE-ID" and files_sent is None


def test_video_note_text_goes_as_second_message(gp_client, db, student, fake_tg, s3_store, no_transcode):
    client, _ = gp_client
    student(chat=101)
    bid = _create(client)
    files = {"video": ("note.webm", b"NOTE", "video/webm")}
    _save(client, bid, action="preview", files=files, video_note="1")
    client.post(f"/cabinet/staff/broadcasts/{bid}/send")
    sent = fake_tg.to(101)
    assert [m for m, _, _ in sent] == ["sendVideoNote", "sendMessage"]
    assert "caption" not in sent[0][1] and sent[0][1]["video_note"] == "NOTE-ID"
    assert sent[1][1]["text"] == "<b>Привет</b>"


def test_caption_limit_and_single_attachment(gp_client, db, s3_store, no_transcode):
    client, _ = gp_client
    bid = _create(client)
    long_text = "а" * (bc.CAPTION_LIMIT + 1)
    resp = _save(client, bid, text=long_text, files={"audio": ("r.webm", b"x", "audio/webm")})
    assert "error=" in resp.headers["location"]
    db.expire_all()
    assert db.get(Broadcast, bid).media_kind is None  # отказ ничего не сохранил
    assert "error" not in _save(client, bid, text=long_text).headers["location"]  # без вложения — 4096

    resp = _save(client, bid, files={
        "audio": ("r.webm", b"x", "audio/webm"), "video": ("n.webm", b"y", "video/webm"),
    })
    assert "error=" in resp.headers["location"]


def test_blocked_bot_and_rate_limit(gp_client, db, student, fake_tg):
    client, _ = gp_client
    student(chat=101, last_name="А")
    student(chat=102, last_name="Б")
    bid = _create(client)
    _save(client, bid, action="preview")
    fake_tg.fail_chats[101] = telegram_service.ApiResult(
        ok=False, status=403, description="Forbidden: bot was blocked by the user",
    )
    fake_tg.queue = [telegram_service.ApiResult(ok=False, status=429, retry_after=1)]
    client.post(f"/cabinet/staff/broadcasts/{bid}/send")
    db.expire_all()
    rows = {r.status: r for r in db.query(BroadcastRecipient).filter_by(broadcast_id=bid)}
    assert rows[RECIPIENT_FAILED].error == "Ученик заблокировал бота"
    assert RECIPIENT_SENT in rows  # после 429 — повтор и доставка
    assert db.get(Broadcast, bid).status == STATUS_SENT


def test_resume_only_after_stalled_pass(db, student, user_factory, fake_tg):
    gp = user_factory(vk_id=880_002, role_name="админ")
    s = student(chat=101)
    broadcast = Broadcast(created_by_id=gp.id, text="x", status=STATUS_SENDING,
                          sending_started_at=datetime.now(timezone.utc))
    db.add(broadcast)
    db.commit()
    db.add(BroadcastRecipient(broadcast_id=broadcast.id, user_id=s.id))
    db.commit()
    assert not bc.claim_resume(db, broadcast.id)
    broadcast.sending_started_at = datetime.now(timezone.utc) - timedelta(minutes=11)
    db.commit()
    assert bc.claim_resume(db, broadcast.id)
    asyncio.run(delivery.run_broadcast(broadcast.id))
    db.expire_all()
    assert db.get(Broadcast, broadcast.id).status == STATUS_SENT
    assert [m for m, _, _ in fake_tg.to(101)] == ["sendMessage"]


def test_telegram_button_approval_checks_chat_and_token(gp_client, db, student, fake_tg):
    client, _ = gp_client
    student(chat=101)
    bid = _create(client)
    _save(client, bid, action="preview")
    db.expire_all()
    token = db.get(Broadcast, bid).preview_token

    assert delivery.approve_from_telegram(bid, token, 999)[0] is False
    assert delivery.approve_from_telegram(bid, "чужой", ADMIN_CHAT)[0] is False
    ok, text = delivery.approve_from_telegram(bid, token, ADMIN_CHAT)
    assert ok and "1 ученику" in text
    assert delivery.approve_from_telegram(bid, token, ADMIN_CHAT)[0] is False  # второй раз — нет
    db.expire_all()
    assert db.get(Broadcast, bid).status == STATUS_SENDING


def test_preview_falls_back_to_service_account(db, user_factory, monkeypatch):
    gp = user_factory(vk_id=880_003, role_name="админ")
    fallback = user_factory(vk_id=880_004)
    fallback.telegram_chat_id = 4242
    db.commit()
    monkeypatch.setattr(bc, "BROADCAST_PREVIEW_FALLBACK_IDS", frozenset({fallback.id}))
    assert bc.preview_target(db, gp.id) == (fallback.id, 4242)


def test_delete_only_drafts(gp_client, db, student, fake_tg):
    client, _ = gp_client
    bid = _create(client)
    assert client.post(f"/cabinet/staff/broadcasts/{bid}/delete", follow_redirects=False).status_code == 302
    db.expire_all()
    assert db.get(Broadcast, bid) is None


def test_screens_render_and_audience_counter(gp_client, db, student):
    client, _ = gp_client
    student(chat=101)
    student(chat=None)
    assert client.get("/cabinet/staff/broadcasts").status_code == 200
    bid = _create(client)
    page = client.get(f"/cabinet/staff/broadcasts/{bid}")
    assert page.status_code == 200
    assert "data-tg-editable" in page.text and "Я С ВАМИ" in page.text
    counter = client.get("/cabinet/staff/broadcasts/audience", params={"tariffs": "Я С ВАМИ"}).json()
    assert counter == {"total": 2, "reachable": 1, "no_telegram": 1, "notifications_off": 0}
    assert client.get("/cabinet/staff/broadcasts/audience").json()["total"] == 0


def test_staff_below_head_has_no_access(client, session_factory, user_factory):
    curator = user_factory(vk_id=880_010, role_name="куратор")
    client.cookies.set("session_id", session_factory(curator).id)
    resp = client.get("/cabinet/staff/broadcasts", follow_redirects=False)
    assert resp.status_code in (302, 303, 403, 404)


def test_nav_shows_broadcasts_to_head(gp_client):
    client, _ = gp_client
    page = client.get("/cabinet/staff/broadcasts")
    assert 'href="/cabinet/staff/broadcasts"' in page.text


def test_static_versioned(gp_client, assert_static_versioned):
    client, _ = gp_client
    bid = _create(client)
    assert_static_versioned(client.get(f"/cabinet/staff/broadcasts/{bid}").text)


def test_draft_status_default(db, user_factory):
    gp = user_factory(vk_id=880_005, role_name="админ")
    broadcast = Broadcast(created_by_id=gp.id)
    db.add(broadcast)
    db.commit()
    assert broadcast.status == STATUS_DRAFT and broadcast.text == ""
