"""Кто не оплатил — службе заботы в бот (владелец 15.10.2026).

Служба заботы: «чтобы сразу в бот приходил список не оплативших… а то
заходишь в статистику, копируешь аккаунты, потом заходишь в тг, вставляешь,
ищешь». Два пути, один список:

- **Отчёт утром после закрытия окна.** С `REPORT_HOUR` МСК дня отсечки
  (2:00 после последнего дня окна) — кто из закрывшегося этой ночью окна
  оплатил и кто нет. Уходит уведомлением получателям уведомлений об оплате
  (`PAYMENT_NOTIFY_RECIPIENT_IDS`): в Telegram и в колокольчик. Один на день
  отсечки — отметка `StudentReminder` у получателя.
- **Команда `/neoplata` в меню бота** — список в любой момент: у кого окно
  закрылось без оплаты и у кого окно идёт, а оплаты ещё нет. Отвечает только
  своим (`can_use_command`): получателям уведомлений об оплате, тем, кому
  открыто всё про оплату (`payments.is_payments_staff`), и поимённо
  (`PAYMENT_DEBTORS_COMMAND_EXTRA_IDS`). В меню команда стоит только в их
  чатах (`sync_bot_menu`, раз в час — привязал Telegram, через час кнопка
  в меню), ученики её не видят.

Кто в списке — тот же учёт, что на экране «Оплаты» (`payment_board`):
ученики вне архива, без служебных, с окном оплаты. Должный месяц у каждого
свой (`payments.due_period`), поэтому должник прошлого месяца не прячется
за «окно впереди» нового. Контакт — ник Telegram (в Telegram по нему сразу
открывается чат), без ника — почта.
"""
from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy.orm import Session as DBSession

from app.cache import invalidate_unread
from app.config import settings
from app.constants import PAYMENT_DEBTORS_COMMAND_EXTRA_IDS, PAYMENT_NOTIFY_RECIPIENT_IDS
from app.models.notification import Notification
from app.models.student_reminder import KIND_PAY_DEBT_REPORT, StudentReminder
from app.models.user import User
from app.services import payments
from app.services import telegram as telegram_service
from app.services.contacts import normalize_tg_username
from app.services.payment_board import accounted_students, display_name
from app.services.tz import MSK_TZ

logger = logging.getLogger(__name__)

BOT_COMMAND = "neoplata"
BOT_COMMAND_TEXT = "Кто не оплатил обучение"
# Отчёт утром дня отсечки — не ночью, когда окно только закрылось.
REPORT_HOUR = 10
# Telegram режет сообщение на 4096 символах; список делится по строкам.
MESSAGE_LIMIT = 3800
# В уведомлении (колокольчик + Telegram одним сообщением) — не больше
# стольких строк, дальше — «и ещё N, весь список — /neoplata».
REPORT_LIST_LIMIT = 50


@dataclass
class Debtor:
    student: User
    amount_kop: int | None
    window: str            # «10–15»
    last_day: str          # «15.10» — последний день окна должного месяца
    cutoff: datetime       # отсечка окна должного месяца, UTC


def _debtor(db: DBSession, student: User, now: datetime) -> Debtor | None:
    """Должный месяц ученика, если его окно уже открылось."""
    period = payments.due_period(student, now)
    if now < payments.window_opens_at(student, period):
        return None
    return Debtor(
        student=student,
        amount_kop=payments.price_kop(db, student),
        window=f"{student.pay_window_start}–{student.pay_window_end}",
        last_day=payments.window_last_day(student, period).strftime("%d.%m"),
        cutoff=payments.window_cutoff(student, period),
    )


def debtors(db: DBSession, now: datetime) -> tuple[list[Debtor], list[Debtor]]:
    """(окно закрылось без оплаты, окно идёт и не оплачено)."""
    overdue, open_ = [], []
    for student in accounted_students(db):
        if not payments.has_window(student):
            continue
        row = _debtor(db, student, now)
        if row is None:
            continue
        (overdue if now >= row.cutoff else open_).append(row)
    return overdue, open_


def _contact(student: User) -> str:
    tg = normalize_tg_username(student.tg_username or "")
    if tg:
        return f"@{tg}"
    return student.email or "контакта нет"


def _line(n: int, row: Debtor, *, escape: bool) -> str:
    name = display_name(row.student) or f"#{row.student.id}"
    contact = _contact(row.student)
    if escape:
        name, contact = html.escape(name), html.escape(contact)
    amount = f" – {payments.rub_text(row.amount_kop)}" if row.amount_kop else ""
    return f"{n}. {name} – {contact}{amount}, окно {row.window}"


def _closed_heading(count: int) -> str:
    tail = ", уроки закрыты" if settings.payments_block_enabled else ""
    return f"Окно закрылось, не оплатили{tail}: {count}"


def command_messages(db: DBSession, now: datetime) -> list[str]:
    """Ответ на `/neoplata` — одно или несколько сообщений (HTML)."""
    overdue, open_ = debtors(db, now)
    stamp = now.astimezone(MSK_TZ).strftime("%d.%m %H:%M")
    if not overdue and not open_:
        return [f"<b>Не оплатили</b> – на {stamp} МСК никого: все, у кого окно открыто или закрылось, оплатили."]
    lines = [f"<b>Не оплатили</b> – на {stamp} МСК"]
    if overdue:
        lines += ["", f"<b>{_closed_heading(len(overdue))}</b>"]
        lines += [_line(i, r, escape=True) for i, r in enumerate(overdue, 1)]
    if open_:
        lines += ["", f"<b>Окно идёт, оплаты пока нет: {len(open_)}</b>"]
        lines += [
            f"{_line(i, r, escape=True)}, до {r.last_day}" for i, r in enumerate(open_, 1)
        ]
    return _split(lines)


def _split(lines: list[str]) -> list[str]:
    chunks, current = [], ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MESSAGE_LIMIT and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


# ── Отчёт утром дня отсечки ─────────────────────────────────────────────────

def _cutoff_day(value: datetime) -> str:
    return value.astimezone(MSK_TZ).date().isoformat()


def _report_text(unpaid: list[Debtor], paid_count: int) -> tuple[str, str]:
    windows = sorted({row.window for row in unpaid}) if unpaid else []
    window = f" {', '.join(windows)}" if windows else ""
    if not unpaid:
        return "Окно оплаты закрылось: все оплатили", f"Оплатили: {paid_count}."
    title = f"Окно оплаты{window} закрылось: не оплатили {len(unpaid)}"
    head = f"Оплатили: {paid_count}. {_closed_heading(len(unpaid))}"
    lines = [_line(i, r, escape=False) for i, r in enumerate(unpaid[:REPORT_LIST_LIMIT], 1)]
    rest = len(unpaid) - REPORT_LIST_LIMIT
    if rest > 0:
        lines.append(f"и ещё {rest} – весь список по команде /{BOT_COMMAND}")
    return title, "\n".join([head, ""] + lines)


def run_debt_report(db: DBSession, now: datetime | None = None) -> list[int]:
    """Отчёт за окно, закрывшееся этой ночью. Коммитит сам; возвращает id
    уведомлений — рассылку делает вызывающий после коммита (`notify_many_sync`).

    Чьё окно закрылось сегодня: у неоплатившего — отсечка должного месяца, у
    оплатившего вовремя — отсечка прошлого (оплата сдвинула должный месяц).
    Ни у кого окно сегодня не закрывалось — отчёта нет."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(MSK_TZ).date()
    if now < datetime.combine(today, time(REPORT_HOUR), MSK_TZ):
        return []
    recipients = [
        uid for uid in sorted(PAYMENT_NOTIFY_RECIPIENT_IDS) if db.get(User, uid) is not None
    ]
    ref = today.isoformat()
    sent = {
        row.user_id for row in db.query(StudentReminder.user_id).filter(
            StudentReminder.kind == KIND_PAY_DEBT_REPORT, StudentReminder.ref == ref,
        )
    }
    recipients = [uid for uid in recipients if uid not in sent]
    if not recipients:
        return []

    unpaid, paid_count = [], 0
    for student in accounted_students(db):
        if not payments.has_window(student):
            continue
        period = payments.due_period(student, now)
        cutoff = payments.window_cutoff(student, period)
        if _cutoff_day(cutoff) == ref and now >= cutoff:
            row = _debtor(db, student, now)
            if row is not None:
                unpaid.append(row)
        elif _cutoff_day(payments.window_cutoff(student, payments.add_months(period, -1))) == ref:
            paid_count += 1
    if not unpaid and not paid_count:
        return []

    title, text = _report_text(unpaid, paid_count)
    created = []
    for uid in recipients:
        notif = Notification(user_id=uid, title=title[:200], text=text)
        db.add(notif)
        created.append(notif)
        db.add(StudentReminder(user_id=uid, kind=KIND_PAY_DEBT_REPORT, ref=ref))
    db.commit()
    for uid in recipients:
        invalidate_unread(uid)
    logger.info("Отчёт о неоплативших: %d не оплатили, %d оплатили", len(unpaid), paid_count)
    return [n.id for n in created]


# ── Команда в боте ──────────────────────────────────────────────────────────

def can_use_command(user: User | None) -> bool:
    if user is None or not user.is_active or user.deleted_at is not None:
        return False
    if user.id in PAYMENT_NOTIFY_RECIPIENT_IDS or user.id in PAYMENT_DEBTORS_COMMAND_EXTRA_IDS:
        return True
    return payments.is_payments_staff({
        "user_id": user.id, "role_name": user.role.name if user.role else None,
    })


async def handle_command(db: DBSession, chat_id: int) -> None:
    """`/neoplata` из чата. Чужим — молчание, как на любой незнакомый текст."""
    user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
    if not can_use_command(user):
        return
    for text in command_messages(db, datetime.now(timezone.utc)):
        await telegram_service.send_message(chat_id, text)


def menu_chat_ids(db: DBSession) -> list[int]:
    """Чаты, где команда стоит в меню бота."""
    users = db.query(User).filter(User.telegram_chat_id.isnot(None)).all()
    return sorted(u.telegram_chat_id for u in users if can_use_command(u))


async def sync_bot_menu(chat_ids: list[int]) -> None:
    """Команда в меню — только в этих чатах (`setMyCommands` с областью
    «чат»): общее меню бота пустое, ученикам добавлять нечего."""
    commands = json.dumps([{"command": BOT_COMMAND, "description": BOT_COMMAND_TEXT}],
                          ensure_ascii=False)
    for chat_id in chat_ids:
        result = await telegram_service.call_api("setMyCommands", {
            "commands": commands,
            "scope": json.dumps({"type": "chat", "chat_id": chat_id}),
        })
        if not result.ok:
            logger.warning("setMyCommands chat_id=%s: %s", chat_id, result.description)
