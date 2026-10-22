"""Рассылки ученикам через бота (владелец 07.10.2026) — `services/broadcasts.py`,
`services/broadcast_delivery.py`, экран `api/cabinet_broadcasts.py`.

Telegram подменяется фейком `FakeTelegram`: он запоминает вызовы и отвечает
как Bot API, включая `file_id` загруженного файла.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

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
from app.services.broadcasts import BroadcastAudience

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
    """Суперадмин с Telegram: раздел пока только его (владелец 08.10.2026).
    Имя фикстуры историческое — так её зовут все тесты пути."""
    sa = user_factory(vk_id=880_001, name="СА", role_name="суперадмин")
    sa.telegram_chat_id = ADMIN_CHAT
    db.commit()
    sess = session_factory(sa)
    client.cookies.set("session_id", sess.id)
    return client, sa


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
        return {u.id for u in bc.audience_users(db, BroadcastAudience(**kw))}

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
    by_tariff = {u.id for u in bc.audience_users(db, BroadcastAudience(tariffs=frozenset({"Я С ВАМИ"})))}
    assert by_tariff == {active.id}
    assert blocked.id not in by_tariff and archived.id not in by_tariff and expired.id not in by_tariff
    named = {u.id for u in bc.audience_users(db, BroadcastAudience(user_ids=frozenset({service_id})))}
    assert named == {service_id}  # служебный — только поимённо, для проверки


def test_excluded_beats_tariff_and_name(db, student):
    """Владелец 22.10.2026: «исключать по юзернейму» — исключённый не получит
    ни по тарифу, ни поимённо; одних исключённых выбором не считаем."""
    stays = student(chat=1)
    out = student(chat=2)
    named_out = student(tariff="Я САМ", chat=3)

    def ids(**kw):
        return {u.id for u in bc.audience_users(db, BroadcastAudience(**kw))}

    assert ids(tariffs=frozenset({"Я С ВАМИ"}), excluded_ids=frozenset({out.id})) == {stays.id}
    assert ids(user_ids=frozenset({named_out.id}), excluded_ids=frozenset({named_out.id})) == set()
    assert ids(excluded_ids=frozenset({out.id})) == set()


def test_excluded_saved_counted_and_never_sent(gp_client, db, student, fake_tg):
    client, _ = gp_client
    stays = student(chat=101)
    out = student(chat=102, tg_username="out_nick")
    bid = _create(client)

    counter = client.get("/cabinet/staff/broadcasts/audience", params={
        "tariffs": "Я С ВАМИ", "excluded": str(out.id),
    }).json()
    assert (counter["total"], counter["reachable"]) == (1, 1)

    # И в «поимённо», и в «кроме» — остаётся только исключённым.
    _save(client, bid, excluded_ids=str(out.id), student_ids=str(out.id))
    db.expire_all()
    audience = bc.get_audience(db, bid)
    assert audience.excluded_ids == {out.id} and audience.user_ids == frozenset()
    page = client.get(f"/cabinet/staff/broadcasts/{bid}").text
    assert f'name="excluded_ids" value="{out.id}"' in page

    resp = _save(client, bid, excluded_ids=str(out.id), action="preview")
    assert resp.headers["location"].endswith("ok=preview")
    assert "кроме 1: Имя " in fake_tg.to(ADMIN_CHAT)[1][1]["text"]
    assert "@out_nick" in fake_tg.to(ADMIN_CHAT)[1][1]["text"]
    # Снять исключение — новая версия, нужна новая проверка.
    _save(client, bid)
    db.expire_all()
    assert not bc.preview_is_current(db, db.get(Broadcast, bid))

    _save(client, bid, excluded_ids=str(out.id), action="preview")
    resp = client.post(f"/cabinet/staff/broadcasts/{bid}/send", follow_redirects=False)
    assert resp.headers["location"].endswith("ok=sending")
    db.expire_all()
    journal = {r.user_id for r in db.query(BroadcastRecipient).filter_by(broadcast_id=bid)}
    assert journal == {stays.id}
    assert fake_tg.to(102) == [] and len(fake_tg.to(101)) == 1


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
    data = {"text": "<b>Привет</b>", "tariffs": ["Я С ВАМИ"], "action": action, "preview_to": ""}
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
    # Отправка только на сайте (владелец 14.10.2026): под проверкой кнопок нет,
    # в подписи — ссылка на экран рассылки.
    assert "reply_markup" not in to_admin[1][1]
    assert f"/cabinet/staff/broadcasts/{bid}" in to_admin[1][1]["text"]
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

    assert client.get(f"/cabinet/staff/broadcasts/{bid}").status_code == 200
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


def _png(width: int, height: int) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 120, 40)).save(buf, "PNG")
    return buf.getvalue()


def test_photo_keeps_full_size_for_telegram(gp_client, db, student, fake_tg, s3_store):
    """Жалоба 12.10.2026 «фото в рассылке плохого качества»: раньше фото
    ужималось до 1600 px и JPEG 82, а потом его ещё раз сжимал Telegram.
    Теперь до 2560 px пиксели не трогаются — Telegram получает то же, что
    прислал преподаватель."""
    import io

    from PIL import Image

    client, _ = gp_client
    student(chat=101)
    bid = _create(client)
    files = {"photo": ("big.png", _png(2400, 1800), "image/png")}
    assert _save(client, bid, action="preview", files=files).headers["location"].endswith("ok=preview")

    (stored,) = s3_store.values()
    assert Image.open(io.BytesIO(stored)).size == (2400, 1800)
    upload = fake_tg.to(ADMIN_CHAT)[0]
    assert upload[0] == "sendPhoto" and upload[2]["photo"][1] == stored


def test_unreadable_photo_is_an_error_not_a_broken_attachment(gp_client, db, s3_store):
    client, _ = gp_client
    bid = _create(client)
    resp = _save(client, bid, files={"photo": ("x.jpg", b"not an image", "image/jpeg")})
    assert "error=" in resp.headers["location"]
    assert s3_store == {}
    db.expire_all()
    assert db.get(Broadcast, bid).media_kind is None


def test_caption_limit_and_single_attachment(gp_client, db, s3_store, no_transcode):
    client, _ = gp_client
    bid = _create(client)
    long_text = "а" * (bc.CAPTION_LIMIT + 1)
    resp = _save(client, bid, text=long_text, files={"audio": ("r.webm", b"x", "audio/webm")})
    assert "error=" in resp.headers["location"] and "ok=saved" in resp.headers["location"]
    db.expire_all()
    saved = db.get(Broadcast, bid)
    # С 08.10.2026 ошибка не стирает набранное: и текст, и голосовое на месте.
    assert saved.media_kind == "voice" and bc.visible_length(saved.text) == bc.CAPTION_LIMIT + 1
    # А в Telegram длинная подпись не уйдёт — ни проверкой, ни рассылкой.
    resp = _save(client, bid, action="preview", text=long_text)
    assert "error=" in resp.headers["location"] and "ok=preview" not in resp.headers["location"]
    # Без вложения — до 4096, ошибки нет.
    assert "error" not in _save(client, bid, text=long_text, remove_media="1").headers["location"]

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


def test_preview_falls_back_to_service_account(db, user_factory, monkeypatch):
    gp = user_factory(vk_id=880_003, role_name="админ")
    fallback = user_factory(vk_id=880_004)
    fallback.telegram_chat_id = 4242
    owner = user_factory(vk_id=880_006)
    owner.telegram_chat_id = 4343
    db.commit()
    monkeypatch.setattr(bc, "BROADCAST_PREVIEW_FALLBACK_IDS", frozenset({fallback.id}))
    monkeypatch.setattr(bc, "REPORT_EXCLUDED_USER_IDS", frozenset({fallback.id, owner.id}))
    assert bc.preview_target(db, gp.id) == (fallback.id, 4242)  # по умолчанию — запасной
    assert bc.preview_target(db, gp.id, owner.id) == (owner.id, 4343)  # выбранный
    assert bc.preview_target(db, gp.id, 999_999) == (fallback.id, 4242)  # чужой id — нет


def test_preview_goes_to_chosen_service_account(gp_client, db, student, user_factory, fake_tg, monkeypatch):
    """Владелец тестирует у себя (аккаунт 199), а не в Telegram Лизы."""
    client, sa = gp_client
    sa.telegram_chat_id = None
    owner = user_factory(vk_id=880_007)
    owner.telegram_chat_id = 6060
    liza = user_factory(vk_id=880_008)
    liza.telegram_chat_id = 7070
    db.commit()
    monkeypatch.setattr(bc, "BROADCAST_PREVIEW_FALLBACK_IDS", frozenset({liza.id}))
    monkeypatch.setattr(bc, "REPORT_EXCLUDED_USER_IDS", frozenset({liza.id, owner.id}))
    student(chat=101)
    bid = _create(client)
    page = client.get(f"/cabinet/staff/broadcasts/{bid}")
    assert f'value="{owner.id}"' in page.text and f'value="{liza.id}"' in page.text
    resp = _save(client, bid, action="preview", preview_to=str(owner.id))
    assert resp.headers["location"].endswith("ok=preview")
    assert fake_tg.to(6060) and not fake_tg.to(7070)


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
    counter = client.get("/cabinet/staff/broadcasts/audience", params={"tariffs": "Я С ВАМИ"}).json()
    assert counter == {
        "total": 2, "reachable": 1, "no_telegram": 1, "notifications_off": 0, "east_offset": 0,
    }
    assert client.get("/cabinet/staff/broadcasts/audience").json()["total"] == 0


def test_staff_below_superadmin_has_no_access(client, session_factory, user_factory):
    for vk_id, role in ((880_010, "куратор"), (880_011, "админ")):
        staff = user_factory(vk_id=vk_id, role_name=role)
        client.cookies.set("session_id", session_factory(staff).id)
        resp = client.get("/cabinet/staff/broadcasts", follow_redirects=False)
        assert resp.status_code in (302, 303, 403, 404), role
    assert client.get("/cabinet/students").status_code == 200


def test_head_sees_broadcasts_after_grant_in_access_screen(client, session_factory, user_factory, db):
    """Лизе раздел открывает суперадмин в «Доступах» — без выкатки."""
    from app.models.section_access import SectionAccessRule

    gp = user_factory(vk_id=880_012, role_name="админ")
    db.add(SectionAccessRule(section_key="broadcasts", user_id=gp.id, level="edit"))
    db.commit()
    client.cookies.set("session_id", session_factory(gp).id)
    page = client.get("/cabinet/staff/broadcasts")
    assert page.status_code == 200
    assert client.post("/cabinet/staff/broadcasts", follow_redirects=False).status_code == 302


# ── Кнопка «Отправить» под старыми проверками (вебхук) ──────────────────────

WEBHOOK_SECRET = "broadcast-test-secret"


def _press(client, data: str, *, chat=ADMIN_CHAT, from_id=ADMIN_CHAT):
    return client.post(
        "/auth/telegram/webhook",
        json={
            "update_id": 1,
            "callback_query": {
                "id": "cb1",
                "from": {"id": from_id},
                "data": data,
                "message": {"message_id": 77, "chat": {"id": chat}},
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )


def test_old_preview_button_sends_nothing(gp_client, db, student, fake_tg, monkeypatch):
    """Проверки до 14.10.2026 несут кнопку «Отправить»: нажатие только
    отвечает, что отправка на сайте, рассылка остаётся черновиком."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "telegram_webhook_secret", WEBHOOK_SECRET)
    client, _ = gp_client
    student(chat=101)
    bid = _create(client)
    _save(client, bid, action="preview")

    assert _press(client, f"bc:{bid}:старый-токен").status_code == 200
    db.expire_all()
    assert db.get(Broadcast, bid).status == STATUS_DRAFT
    assert fake_tg.to(101) == []
    answers = [d for m, d, _ in fake_tg.calls if m == "answerCallbackQuery"]
    assert answers[-1]["text"] == delivery.OLD_BUTTON_ANSWER


def test_webhook_ignores_unknown_buttons(client, fake_tg, monkeypatch):
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "telegram_webhook_secret", WEBHOOK_SECRET)
    assert _press(client, "что-то-другое").status_code == 200
    assert [m for m, _, _ in fake_tg.calls] == ["answerCallbackQuery"]


# ── Колокольчик (владелец 08.10.2026: «нужен тоже») ─────────────────────────


def test_bell_copy_for_every_recipient_without_second_telegram(
    gp_client, db, student, fake_tg, session_factory,
):
    from app.models.notification import Notification

    client, _ = gp_client
    reachable = student(chat=101)
    no_bot = student(chat=None)
    muted = student(chat=103, notifications=False)
    bid = _create(client)
    _save(client, bid, text='<b>Привет</b> <span class="tg-spoiler">тайна</span>', action="preview")
    client.post(f"/cabinet/staff/broadcasts/{bid}/send")

    bells = db.query(Notification).filter(Notification.broadcast_id == bid).all()
    assert {n.user_id for n in bells} == {reachable.id, no_bot.id, muted.id}
    assert {n.title for n in bells} == {bc.BELL_TITLE}
    assert bells[0].text == "Привет тайна"  # попапу — простой текст
    assert [m for m, _, _ in fake_tg.to(101)] == ["sendMessage"]  # колокольчик Telegram не дублирует
    assert fake_tg.to(103) == []

    # Повторный проход («Дослать») второй копии не заводит.
    asyncio.run(delivery.run_broadcast(bid))
    assert db.query(Notification).filter(Notification.broadcast_id == bid).count() == 3

    client.cookies.set("session_id", session_factory(no_bot).id)
    page = client.get("/cabinet/notifications")
    assert page.status_code == 200
    feed = client.get("/cabinet/notifications/feed").json()["notifications"]
    assert feed[0]["href"] == "/cabinet/notifications"


def test_bell_shows_attachment(gp_client, db, student, fake_tg, s3_store, no_transcode, session_factory):
    client, _ = gp_client
    pupil = student(chat=101)
    bid = _create(client)
    _save(client, bid, text="", action="preview", files={"audio": ("rec.webm", b"OGG", "audio/webm")})
    client.post(f"/cabinet/staff/broadcasts/{bid}/send")
    client.cookies.set("session_id", session_factory(pupil).id)
    feed = client.get("/cabinet/notifications/feed").json()["notifications"]
    assert feed[0]["text"] == "Голосовое сообщение"  # без текста — попапу вид вложения
    assert client.get("/cabinet/notifications").status_code == 200


# ── Отложенная отправка ─────────────────────────────────────────────────────


def _msk_input(delta: timedelta) -> str:
    from app.services.tz import now_msk
    return (now_msk() + delta).strftime("%Y-%m-%dT%H:%M")


def _shift_schedule(db, bid, *, scheduled_at, fire_at):
    """Время «пришло»: сдвинуть назначенное, не трогая проверенную версию."""
    broadcast = db.get(Broadcast, bid)
    broadcast.scheduled_at = scheduled_at
    broadcast.fire_at = fire_at
    db.commit()


def test_schedule_msk_waits_for_time_then_sends(gp_client, db, student, fake_tg):
    from app.models.broadcast import STATUS_SCHEDULED

    client, gp = gp_client
    student(chat=101)
    bid = _create(client)
    when = _msk_input(timedelta(hours=2))
    _save(client, bid, action="preview", schedule_mode="msk", scheduled_at=when)
    note = fake_tg.to(ADMIN_CHAT)[1][1]

    resp = client.post(f"/cabinet/staff/broadcasts/{bid}/send", follow_redirects=False)
    assert resp.headers["location"].endswith("ok=scheduled")
    db.expire_all()
    broadcast = db.get(Broadcast, bid)
    assert broadcast.status == STATUS_SCHEDULED and broadcast.sent_by_id == gp.id
    assert bc._aware(broadcast.fire_at) == bc._aware(broadcast.scheduled_at)
    assert fake_tg.to(101) == []
    assert db.query(BroadcastRecipient).filter_by(broadcast_id=bid).count() == 0

    # Запланированную не поправить — только отменить.
    assert "error=" in _save(client, bid, text="другое").headers["location"]
    assert client.get(f"/cabinet/staff/broadcasts/{bid}").status_code == 200

    asyncio.run(delivery.run_due_broadcasts())  # время не пришло
    assert fake_tg.to(101) == []

    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    _shift_schedule(db, bid, scheduled_at=past, fire_at=past)
    asyncio.run(delivery.run_due_broadcasts())
    db.expire_all()
    assert db.get(Broadcast, bid).status == STATUS_SENT
    assert [m for m, _, _ in fake_tg.to(101)] == ["sendMessage"]


def test_schedule_local_goes_in_waves_by_student_timezone(gp_client, db, student, fake_tg):
    from app.models.notification import Notification

    client, _ = gp_client
    east = student(chat=201, timezone="4")
    msk = student(chat=202, timezone=None)
    west = student(chat=203, timezone="-1")
    bid = _create(client)
    _save(client, bid, action="preview", schedule_mode="local", scheduled_at=_msk_input(timedelta(hours=10)))
    client.post(f"/cabinet/staff/broadcasts/{bid}/send")
    db.expire_all()
    broadcast = db.get(Broadcast, bid)
    chosen = bc._aware(broadcast.scheduled_at)
    assert bc._aware(broadcast.fire_at) == chosen - timedelta(hours=4)  # первым — восток
    assert bc.due_moment(broadcast, east) == chosen - timedelta(hours=4)
    assert bc.due_moment(broadcast, msk) == chosen  # пояс не указан — по Москве
    assert bc.due_moment(broadcast, west) == chosen + timedelta(hours=1)

    now = datetime.now(timezone.utc)
    _shift_schedule(db, bid, scheduled_at=now + timedelta(hours=3), fire_at=now - timedelta(hours=1))
    asyncio.run(delivery.run_due_broadcasts())
    db.expire_all()
    assert fake_tg.to(201) and not fake_tg.to(202) and not fake_tg.to(203)
    bells = {n.user_id for n in db.query(Notification).filter(Notification.broadcast_id == bid)}
    assert bells == {east.id}  # колокольчик — тоже в его время
    broadcast = db.get(Broadcast, bid)
    assert broadcast.status == STATUS_SENDING and broadcast.sending_started_at is None
    assert not bc.can_resume(db, broadcast)  # следующие волны — не «прервалось»
    assert client.get(f"/cabinet/staff/broadcasts/{bid}").status_code == 200

    asyncio.run(delivery.run_due_broadcasts())  # следующая волна ещё не пришла
    assert not fake_tg.to(202)

    for row in db.query(BroadcastRecipient).filter_by(broadcast_id=bid, user_id=msk.id):
        row.due_at = now - timedelta(minutes=1)
    db.commit()
    asyncio.run(delivery.run_due_broadcasts())
    assert fake_tg.to(202) and not fake_tg.to(203)

    for row in db.query(BroadcastRecipient).filter_by(broadcast_id=bid, user_id=west.id):
        row.due_at = now - timedelta(minutes=1)
    db.commit()
    asyncio.run(delivery.run_due_broadcasts())
    db.expire_all()
    assert db.get(Broadcast, bid).status == STATUS_SENT
    assert [len(fake_tg.to(chat)) for chat in (201, 202, 203)] == [1, 1, 1]  # каждому один раз
    assert db.query(Notification).filter(Notification.broadcast_id == bid).count() == 3


def test_schedule_refuses_past_time(gp_client, db, student, fake_tg):
    client, _ = gp_client
    student(chat=101, timezone="4")
    bid = _create(client)
    resp = _save(client, bid, action="preview", schedule_mode="msk", scheduled_at=_msk_input(-timedelta(hours=1)))
    assert "error=" in resp.headers["location"] and fake_tg.calls == []
    # Через 2 часа по Москве — у МСК+4 это время уже прошло.
    resp = _save(client, bid, action="preview", schedule_mode="local", scheduled_at=_msk_input(timedelta(hours=2)))
    assert "error=" in resp.headers["location"] and fake_tg.calls == []
    assert "error=" in _save(client, bid, schedule_mode="msk", scheduled_at="").headers["location"]


def test_cancel_schedule_returns_draft(gp_client, db, student, fake_tg):
    client, _ = gp_client
    student(chat=101)
    bid = _create(client)
    _save(client, bid, action="preview", schedule_mode="msk", scheduled_at=_msk_input(timedelta(hours=2)))
    client.post(f"/cabinet/staff/broadcasts/{bid}/send")
    resp = client.post(f"/cabinet/staff/broadcasts/{bid}/unschedule", follow_redirects=False)
    assert resp.headers["location"].endswith("ok=unscheduled")
    db.expire_all()
    broadcast = db.get(Broadcast, bid)
    assert broadcast.status == STATUS_DRAFT and broadcast.fire_at is None
    again = client.post(f"/cabinet/staff/broadcasts/{bid}/unschedule", follow_redirects=False)
    assert "error=" in again.headers["location"]
    # Снова черновик: правится, а смена времени требует новой проверки.
    _save(client, bid, schedule_mode="msk", scheduled_at=_msk_input(timedelta(hours=3)))
    db.expire_all()
    assert not bc.preview_is_current(db, db.get(Broadcast, bid))


def test_save_error_keeps_everything_else(gp_client, db, student, fake_tg):
    """Владелец 08.10.2026: ошибка в одном поле не стирает остальное. До этого
    «по Москве» без времени возвращало страницу без записи — текст, выбор
    учеников и режим пропадали."""
    client, _ = gp_client
    picked = student(chat=101)
    bid = _create(client)
    resp = _save(
        client, bid, text="<b>Новый текст</b>", tariffs=[], student_ids=str(picked.id),
        schedule_mode="msk", scheduled_at="",
    )
    location = unquote(resp.headers["location"])
    db.expire_all()
    saved = db.get(Broadcast, bid)
    assert saved.text == "<b>Новый текст</b>" and saved.schedule_mode == "msk"
    assert saved.scheduled_at is None
    assert bc.get_audience(db, bid).user_ids == {picked.id}
    # Страница показывает сохранённое, а не пустую форму.
    assert client.get(f"/cabinet/staff/broadcasts/{bid}?ok=saved&error=x").status_code == 200


def test_preview_with_save_error_is_not_sent(gp_client, db, student, fake_tg):
    """Прошедшее время ловится уже при сохранении, и проверка не уходит."""
    client, _ = gp_client
    student(chat=101)
    bid = _create(client)
    resp = _save(client, bid, action="preview", schedule_mode="msk", scheduled_at="2020-01-01T10:00")
    location = unquote(resp.headers["location"])
    assert fake_tg.to(ADMIN_CHAT) == []
    db.expire_all()
    assert db.get(Broadcast, bid).schedule_mode == "msk"


# ── Вложение: миниатюра, «Заменить» и «Удалить» (владелец 08.10.2026) ───────


def test_saved_attachment_is_thumbnail_with_replace_and_remove(
    gp_client, db, student, fake_tg, s3_store, no_transcode,
):
    client, _ = gp_client
    student(chat=101)
    bid = _create(client)
    _save(client, bid, files={"video": ("note.webm", b"NOTE", "video/webm")}, video_note="1")
    assert client.get(f"/cabinet/staff/broadcasts/{bid}").status_code == 200

    # «Удалить» на странице — это remove_media=1 при «Сохранить».
    _save(client, bid, remove_media="1")
    db.expire_all()
    assert db.get(Broadcast, bid).media_kind is None
