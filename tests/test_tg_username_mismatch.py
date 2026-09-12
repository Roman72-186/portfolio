"""Расхождение User.tg_username с живым Telegram-ником (12.09.2026).

Две части: гейт в dependencies.py (закрывает кабинет ученику, кроме «Личной
информации», см. test_access_until.py — тот же приём для access_until) и
сама фоновая проверка exam_scheduler._run_tg_username_check, которая ставит
флаг по ответу Bot API getChat.
"""
from unittest.mock import AsyncMock

import app.api.auth as auth_module
from app.db.database import SessionLocal
from app.dependencies import TG_MISMATCH_DETAIL
from app.models.user import User
from app.services.exam_scheduler import _run_tg_username_check


# ── Гейт: что закрыто и что открыто ──────────────────────────────────────────

def test_mismatched_student_redirected_from_learning(db, client, session_factory, user_factory):
    student = user_factory()
    student.tg_username_mismatch = True
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/learning", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/cabinet/personal"


def test_mismatched_student_can_open_personal(db, client, session_factory, user_factory):
    student = user_factory()
    student.tg_username_mismatch = True
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/personal")

    assert resp.status_code == 200
    assert "Доступ закрыт" in resp.text
    assert "/cabinet/personal/contacts" in resp.text


def test_mismatched_student_api_gets_403_json(db, client, session_factory, user_factory):
    student = user_factory()
    student.tg_username_mismatch = True
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/tracker", headers={"accept": "application/json"})

    assert resp.status_code == 403
    assert resp.json()["detail"] == TG_MISMATCH_DETAIL


def test_no_mismatch_does_not_block(auth_client):
    client, _student = auth_client

    resp = client.get("/cabinet/personal")

    assert resp.status_code == 200
    assert "Доступ закрыт" not in resp.text


def test_mismatch_ignored_when_profile_incomplete(db, client, session_factory, user_factory):
    """Анкета ещё не заполнена — гейта нет (иначе некому дойти даже до формы),
    как и у access_until в test_expired_student_without_profile_does_not_loop."""
    student = user_factory(profile_completed=False)
    student.tg_username_mismatch = True
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    resp = client.get("/cabinet/profile")

    assert resp.status_code == 200


def test_mismatch_ignored_for_staff(db, client, session_factory, user_factory):
    """Случайный флаг на строке сотрудника не должен запирать ему кабинет."""
    staff = user_factory(vk_id=555_002, name="Куратор", role_name="куратор", is_admin=False)
    staff.tg_username_mismatch = True
    db.commit()
    client.cookies.set("session_id", session_factory(staff).id)

    resp = client.get("/cabinet/students", follow_redirects=False)

    assert resp.status_code == 200


# ── Фоновая проверка _run_tg_username_check ─────────────────────────────────

def test_check_flags_mismatch(db, user_factory, monkeypatch):
    student = user_factory()
    student.telegram_chat_id = 900_001
    student.tg_username = "ivan"
    db.commit()

    mock = AsyncMock(return_value=(True, "petrov"))
    monkeypatch.setattr("app.services.exam_scheduler.telegram_service.get_chat_username", mock)

    _run_tg_username_check()

    db.refresh(student)
    assert student.tg_username_mismatch is True


def test_check_clears_mismatch_on_match(db, user_factory, monkeypatch):
    student = user_factory()
    student.telegram_chat_id = 900_002
    student.tg_username = "ivan"
    student.tg_username_mismatch = True
    db.commit()

    mock = AsyncMock(return_value=(True, "ivan"))
    monkeypatch.setattr("app.services.exam_scheduler.telegram_service.get_chat_username", mock)

    _run_tg_username_check()

    db.refresh(student)
    assert student.tg_username_mismatch is False


def test_check_is_case_insensitive(db, user_factory, monkeypatch):
    student = user_factory()
    student.telegram_chat_id = 900_003
    student.tg_username = "Ivan_Petrov"
    db.commit()

    mock = AsyncMock(return_value=(True, "ivan_petrov"))
    monkeypatch.setattr("app.services.exam_scheduler.telegram_service.get_chat_username", mock)

    _run_tg_username_check()

    db.refresh(student)
    assert student.tg_username_mismatch is False


def test_check_treats_missing_live_username_as_mismatch(db, user_factory, monkeypatch):
    """Человек снял публичный ник в настройках Telegram — считаем это
    расхождением, а не «всё в порядке»."""
    student = user_factory()
    student.telegram_chat_id = 900_004
    student.tg_username = "ivan"
    db.commit()

    mock = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("app.services.exam_scheduler.telegram_service.get_chat_username", mock)

    _run_tg_username_check()

    db.refresh(student)
    assert student.tg_username_mismatch is True


def test_check_leaves_state_when_api_fails(db, user_factory, monkeypatch):
    """ok=False — Telegram не ответил, а не «ник неверный»: состояние не трогаем."""
    student = user_factory()
    student.telegram_chat_id = 900_005
    student.tg_username = "ivan"
    student.tg_username_mismatch = True
    db.commit()

    mock = AsyncMock(return_value=(False, None))
    monkeypatch.setattr("app.services.exam_scheduler.telegram_service.get_chat_username", mock)

    _run_tg_username_check()

    db.refresh(student)
    assert student.tg_username_mismatch is True  # не снято, хотя было бы легко ошибочно снять


def test_check_does_not_clobber_concurrent_fix_mid_flight(db, user_factory, monkeypatch):
    """Гонка: пока идёт сетевой обход ночной проверки, ученик через
    /cabinet/personal/contacts сам чинит ник и снимает блокировку отдельной
    сессией/коммитом — итоговый commit ночной задачи не должен затереть это
    устаревшим сравнением (см. db.expire_all() в exam_scheduler)."""
    student = user_factory()
    student.telegram_chat_id = 900_030
    student.tg_username = "old_nick"
    student.tg_username_mismatch = True
    db.commit()
    student_id = student.id

    async def _fake_get_chat_username(chat_id, **kwargs):
        concurrent_db = SessionLocal()
        try:
            concurrent_student = concurrent_db.query(User).filter(User.id == student_id).first()
            concurrent_student.tg_username = "new_nick"
            concurrent_student.tg_username_mismatch = False
            concurrent_db.commit()
        finally:
            concurrent_db.close()
        return True, "new_nick"

    monkeypatch.setattr(
        "app.services.exam_scheduler.telegram_service.get_chat_username",
        _fake_get_chat_username,
    )

    _run_tg_username_check()

    db.refresh(student)
    assert student.tg_username == "new_nick"
    assert student.tg_username_mismatch is False


def test_check_skips_students_without_chat_id(db, user_factory, monkeypatch):
    """Вход только через VK — живого источника правды нет, проверять нечего."""
    student = user_factory()
    student.tg_username = "ivan"
    db.commit()

    mock = AsyncMock(return_value=(True, "кто-то-другой"))
    monkeypatch.setattr("app.services.exam_scheduler.telegram_service.get_chat_username", mock)

    _run_tg_username_check()

    mock.assert_not_called()
    db.refresh(student)
    assert student.tg_username_mismatch is False


# ── Вход через бота сам снимает блокировку ──────────────────────────────────

def test_bot_start_clears_mismatch_when_username_now_synced(db, user_factory):
    """Ученик написал боту /start живым Telegram-аккаунтом — апдейт несёт
    подтверждённый ник, снимаем блокировку тут же, не дожидаясь ночной
    проверки или похода на /cabinet/personal/contacts."""
    student = user_factory()
    student.telegram_chat_id = 900_020
    student.tg_username = "old_nick"
    student.tg_username_mismatch = True
    db.commit()

    tg_from = auth_module._TgFrom(id=900_020, username="new_nick", first_name="Иван")
    auth_module._upsert_telegram_user(
        db, chat_id=900_020, tg_from=tg_from, is_group_member=True,
    )
    db.commit()

    db.refresh(student)
    assert student.tg_username == "new_nick"
    assert student.tg_username_mismatch is False


def test_bot_start_without_username_leaves_mismatch_untouched(db, user_factory):
    """Апдейт без tg_from.username (человек снял публичный ник) не несёт
    подтверждения — блокировку снимать не на основании чего, оставляем как есть."""
    student = user_factory()
    student.telegram_chat_id = 900_021
    student.tg_username = "old_nick"
    student.tg_username_mismatch = True
    db.commit()

    tg_from = auth_module._TgFrom(id=900_021, username=None, first_name="Иван")
    auth_module._upsert_telegram_user(
        db, chat_id=900_021, tg_from=tg_from, is_group_member=True,
    )
    db.commit()

    db.refresh(student)
    assert student.tg_username_mismatch is True


# ── Живая сверка при сохранении контактов ───────────────────────────────────

def test_contacts_save_rejects_when_still_mismatched(db, client, session_factory, user_factory):
    student = user_factory()
    student.telegram_chat_id = 900_010
    student.tg_username = "ivan"
    student.tg_username_mismatch = True
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    mock = AsyncMock(return_value=(True, "drugoy_nik"))
    import app.api.cabinet_personal as cabinet_personal_module
    from unittest.mock import patch
    with patch.object(cabinet_personal_module.telegram_service, "get_chat_username", mock):
        resp = client.post("/cabinet/personal/contacts", data={
            "phone": "+79001112233",
            "parent_phone": "+79002223344",
            "tg_username": "ivan",
        })

    assert resp.status_code == 200
    assert "drugoy_nik" in resp.text
    db.refresh(student)
    assert student.tg_username_mismatch is True


def test_contacts_save_clears_mismatch_when_now_matches(db, client, session_factory, user_factory):
    student = user_factory()
    student.telegram_chat_id = 900_011
    student.tg_username = "old_nick"
    student.tg_username_mismatch = True
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    mock = AsyncMock(return_value=(True, "new_nick"))
    import app.api.cabinet_personal as cabinet_personal_module
    from unittest.mock import patch
    with patch.object(cabinet_personal_module.telegram_service, "get_chat_username", mock):
        resp = client.post("/cabinet/personal/contacts", data={
            "phone": "+79001112233",
            "parent_phone": "+79002223344",
            "tg_username": "new_nick",
        }, follow_redirects=False)

    assert resp.status_code == 302
    db.refresh(student)
    assert student.tg_username_mismatch is False
    assert student.tg_username == "new_nick"


def test_contacts_save_rejects_when_telegram_unreachable_and_blocked(db, client, session_factory, user_factory):
    """Telegram не ответил (ok=False), а ученик всё ещё заблокирован — отказ
    с понятной причиной, а не «Контакты сохранены» рядом с баннером «Доступ
    закрыт». Флаг при этом не трогаем вовсе."""
    student = user_factory()
    student.telegram_chat_id = 900_012
    student.tg_username = "old_nick"
    student.tg_username_mismatch = True
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    mock = AsyncMock(return_value=(False, None))
    import app.api.cabinet_personal as cabinet_personal_module
    from unittest.mock import patch
    with patch.object(cabinet_personal_module.telegram_service, "get_chat_username", mock):
        resp = client.post("/cabinet/personal/contacts", data={
            "phone": "+79001112233",
            "parent_phone": "+79002223344",
            "tg_username": "new_nick",
        })

    assert resp.status_code == 200
    assert "не ответил" in resp.text
    db.refresh(student)
    assert student.tg_username_mismatch is True  # не тронут
    assert student.tg_username == "old_nick"  # ничего не сохранилось


def test_contacts_save_accepts_unverifiable_when_not_blocked(db, client, session_factory, user_factory):
    """Telegram не ответил, но ученик и не был заблокирован — обычное
    редактирование контактов не должно упираться в недоступность стороннего API."""
    student = user_factory()
    student.telegram_chat_id = 900_013
    student.tg_username = "old_nick"
    student.tg_username_mismatch = False
    db.commit()
    client.cookies.set("session_id", session_factory(student).id)

    mock = AsyncMock(return_value=(False, None))
    import app.api.cabinet_personal as cabinet_personal_module
    from unittest.mock import patch
    with patch.object(cabinet_personal_module.telegram_service, "get_chat_username", mock):
        resp = client.post("/cabinet/personal/contacts", data={
            "phone": "+79009998877",
            "parent_phone": "+79002223344",
            "tg_username": "old_nick",
        }, follow_redirects=False)

    assert resp.status_code == 302
    db.refresh(student)
    assert student.phone == "+79009998877"
    assert student.tg_username_mismatch is False
