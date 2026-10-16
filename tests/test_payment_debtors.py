"""Кто не оплатил — службе заботы в бот (`services/payment_debtors.py`,
владелец 15.10.2026): команда `/neoplata`, отчёт утром дня отсечки, меню."""
from datetime import date, datetime, timezone

import pytest

import app.api.auth as auth_module
from app.config import settings
from app.models.notification import Notification
from app.models.payment import COHORT_FROM, PaymentPrice
from app.services import payment_debtors as pd
from app.services import payments as ps
from app.services.tz import MSK_TZ

WEBHOOK_SECRET = "test-webhook-secret"
CARE_CHAT = 555_277


def _msk(*args) -> datetime:
    return datetime(*args, tzinfo=MSK_TZ).astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token")
    monkeypatch.setattr(settings, "payments_block_enabled", True)


@pytest.fixture()
def prices(db):
    db.add(PaymentPrice(tariff="Я С ВАМИ", cohort=COHORT_FROM, amount_kop=1325500))
    db.commit()


def _student(db, user_factory, vk_id, last_name, *, tg=None, email=None, window=(10, 15)):
    student = user_factory(vk_id=vk_id, name=last_name, tariff="Я С ВАМИ")
    student.last_name, student.first_name = last_name, "Анна"
    student.tg_username, student.email = tg, email
    student.pay_window_start, student.pay_window_end = window
    student.pay_cohort = COHORT_FROM
    db.commit()
    return student


@pytest.fixture()
def school(db, user_factory, prices):
    """Окно 10–15: Иванова не оплатила (ник), Петрова не оплатила (только
    почта), Сидорова оплатила октябрь. Окно 25–30: Кузнецова."""
    unpaid_tg = _student(db, user_factory, 300_001, "Иванова", tg="ivanova_a")
    unpaid_mail = _student(db, user_factory, 300_002, "Петрова", email="petrova@example.com")
    paid = _student(db, user_factory, 300_003, "Сидорова", tg="sidorova")
    paid.paid_until = ps.paid_until_after(paid, date(2026, 10, 1))
    later = _student(db, user_factory, 300_004, "Кузнецова", tg="kuz", window=(25, 30))
    db.commit()
    return unpaid_tg, unpaid_mail, paid, later


@pytest.fixture()
def care(db, user_factory, monkeypatch):
    user = user_factory(vk_id=200_277, name="Служба заботы", role_name="куратор")
    user.telegram_chat_id = CARE_CHAT
    db.commit()
    monkeypatch.setattr(pd, "PAYMENT_NOTIFY_RECIPIENT_IDS", frozenset({user.id}))
    return user


def test_command_lists_closed_and_open_windows(db, school):
    unpaid_tg, unpaid_mail, paid, later = school
    text = "\n".join(pd.command_messages(db, _msk(2026, 10, 26, 12, 0)))
    closed, open_ = text.split("Окно идёт")
    assert "Окно закрылось, не оплатили, уроки закрыты: 2" in closed
    assert "Иванова Анна – @ivanova_a – 13 255 ₽, окно 10–15" in closed
    assert "Петрова Анна – petrova@example.com" in closed
    assert "Кузнецова Анна – @kuz – 13 255 ₽, окно 25–30, до 30.10" in open_
    assert "Сидорова" not in text
    # Итог месяца первой строкой после заголовка (владелец 16.10.2026).
    assert "Октябрь: оплатили 1, не оплатили 3" in closed


def test_command_total_counts_upcoming_and_skips_left(db, school):
    unpaid_tg, unpaid_mail, paid, later = school
    unpaid_mail.access_until = _msk(2026, 10, 15, 0, 5)
    db.commit()
    text = "\n".join(pd.command_messages(db, _msk(2026, 10, 16, 12, 0)))
    assert "Октябрь: оплатили 1, не оплатили 2 (у 1 окно ещё не открылось)" in text


def test_command_when_everyone_paid(db, school):
    for student in school:
        student.paid_until = ps.paid_until_after(student, date(2026, 10, 1))
    db.commit()
    assert "никого" in pd.command_messages(db, _msk(2026, 10, 20, 12, 0))[0]


def test_long_list_is_split_under_telegram_limit(db, user_factory, prices):
    for i in range(120):
        _student(db, user_factory, 310_000 + i, f"Ученица{i:03d}", tg=f"student_{i:03d}")
    chunks = pd.command_messages(db, _msk(2026, 10, 20, 12, 0))
    assert len(chunks) > 1
    assert all(len(c) <= pd.MESSAGE_LIMIT for c in chunks)
    assert sum(c.count("@student_") for c in chunks) == 120


def test_debt_report_morning_after_cutoff_once(db, school, care):
    assert pd.run_debt_report(db, _msk(2026, 10, 16, 9, 30)) == []   # рано
    ids = pd.run_debt_report(db, _msk(2026, 10, 16, 10, 0))
    assert len(ids) == 1
    note = db.get(Notification, ids[0])
    assert note.user_id == care.id
    assert note.title == "Окно оплаты 10–15 закрылось: не оплатили 2"
    assert note.text.startswith("Оплатили: 1. Окно закрылось, не оплатили, уроки закрыты: 2")
    assert "@ivanova_a" in note.text and "petrova@example.com" in note.text
    assert "Кузнецова" not in note.text
    assert pd.run_debt_report(db, _msk(2026, 10, 16, 12, 0)) == []   # повтора нет


def test_no_report_when_no_window_closed_today(db, school, care):
    assert pd.run_debt_report(db, _msk(2026, 10, 20, 12, 0)) == []


def _send(client, chat_id, text):
    return client.post(
        "/auth/telegram/webhook",
        json={"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text,
                                           "from": {"id": chat_id, "first_name": "Лиза"}}},
        headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET},
    )


@pytest.fixture()
def sent(monkeypatch):
    calls: list[tuple[int, str]] = []

    async def fake_send_message(chat_id, text, **kwargs):
        calls.append((chat_id, text))
        return True

    monkeypatch.setattr(pd.telegram_service, "send_message", fake_send_message)
    monkeypatch.setattr(auth_module.telegram_service, "send_message", fake_send_message)
    return calls


def test_bot_command_answers_care_only(client, db, school, care, sent):
    assert _send(client, CARE_CHAT, "/neoplata").status_code == 200
    assert len(sent) == 1 and sent[0][0] == CARE_CHAT
    assert "Не оплатили" in sent[0][1]

    stranger = school[0]
    stranger.telegram_chat_id = 777_001
    db.commit()
    _send(client, 777_001, "/neoplata")
    assert len(sent) == 1


def test_menu_only_in_allowed_chats(db, school, care, user_factory):
    school[0].telegram_chat_id = 777_002
    chief = user_factory(vk_id=200_010, name="ГП", role_name="админ")
    chief.telegram_chat_id = 777_010
    db.commit()
    assert pd.menu_chat_ids(db) == sorted([CARE_CHAT, 777_010])


def test_named_account_gets_command_and_menu(db, school, care, user_factory, monkeypatch):
    """Поимённо — владелец 15.10.2026: «мне нужно 199»; по роли он ученик."""
    owner = user_factory(vk_id=200_199, name="Владелец")
    owner.telegram_chat_id = 777_199
    db.commit()
    assert not pd.can_use_command(owner)
    monkeypatch.setattr(pd, "PAYMENT_DEBTORS_COMMAND_EXTRA_IDS", frozenset({owner.id}))
    assert pd.can_use_command(owner)
    assert 777_199 in pd.menu_chat_ids(db)


def test_left_students_not_in_list_or_report(db, school, care):
    """Срок доступа наступил — ушёл, в список не попадает (владелец 16.10.2026).
    Срок впереди — ещё учится, остаётся."""
    unpaid_tg, unpaid_mail, paid, later = school
    unpaid_mail.access_until = _msk(2026, 10, 15, 0, 5)
    later.access_until = _msk(2026, 11, 30, 0, 0)
    db.commit()
    text = "\n".join(pd.command_messages(db, _msk(2026, 10, 26, 12, 0)))
    assert "Петрова" not in text
    assert "Иванова" in text and "Кузнецова" in text

    note = db.get(Notification, pd.run_debt_report(db, _msk(2026, 10, 16, 10, 0))[0])
    assert note.title == "Окно оплаты 10–15 закрылось: не оплатили 1"
    assert "Петрова" not in note.text
