"""Напоминание 12 ученикам вписать ФИО как в паспорте (29.09.2026, разовая кампания).

Предыстория — docs/ops-history.md (запись 2026-09-29 про возврат ФИО): до
`4d17410` вход через Telegram затирал ФИО ником. Двадцати ученикам имя вернули
из ночных дампов, двенадцати вернуть было нечего — им 29.09 ушло уведомление
«Впиши имя и фамилию как в паспорте». Этот скрипт раз в сутки смотрит, кто
исправил, и не чаще раза в 44 часа напоминает остальным, максимум дважды.
Итог каждого напоминания и финальный отчёт уходят владельцу в Telegram
(аккаунт id 199: у суперадмина id 3 чата с ботом нет).

Запускается не как модуль приложения, а подаётся в контейнер через stdin —
обёртка `portfolio-name-reminder.sh`, там же установка. Состояние не хранит:
сколько напоминаний было, считает по таблице `notifications`.

    python - --dry-run   # ничего не пишет и не шлёт, только печатает план
"""
import asyncio
import html
import re
import sys
from datetime import date, datetime, timedelta, timezone

from app.db.database import SessionLocal
from app.models.notification import Notification
from app.models.user import User
from app.services import telegram as tg

DRY_RUN = "--dry-run" in sys.argv
# Только вместе с --dry-run: проверить план на будущий день, например
# `--dry-run --now=2026-10-06T09:00`.
PRETEND_NOW = next(
    (
        datetime.fromisoformat(a.split("=", 1)[1]).replace(tzinfo=timezone.utc)
        for a in sys.argv
        if a.startswith("--now=") and DRY_RUN
    ),
    None,
)

CAMPAIGN_START = datetime(2026, 9, 29, tzinfo=timezone.utc)
# После этой даты скрипт молчит; в сам день — финальный отчёт владельцу.
END_DATE = date(2026, 10, 6)
MAX_REMINDERS = 2
MIN_GAP = timedelta(hours=44)
OWNER_ID = 199

FIRST_TITLE = "Впиши имя и фамилию как в паспорте"
REMINDER_TITLE = "Напоминание: имя и фамилия как в паспорте"
REMINDER_BODY = (
    "В профиле до сих пор стоит «{name}». Открой «Личную информацию» → "
    "«Изменить личные данные», впиши имя и фамилию из паспорта и нажми "
    "«Сохранить»: https://apparchi.ru/cabinet/personal/contacts"
)

# `users.name` в момент первого уведомления. Исправил = имя сменилось и
# похоже на паспортное: «Настя Строкова» проходит проверку формы, но это то же,
# что было, — без сравнения с исходным её сочли бы исправившей.
BASELINE = {
    190: "полина ступина",
    220: "Миха 🐈",
    242: "God_of_Fatigue",
    243: "Ксюня Каргина",
    245: "Ксюша",
    246: "Настя Строкова",
    249: "･✧🎧☕🎀✧･",
    250: "???",
    255: "полина сидорова",
    256: "Попандопуло Попандопуло",
    263: "kk*0 Шевченко",
    269: "Настя Смирнова",
}
PASSPORT_WORD = re.compile(r"^[А-ЯЁ][а-яё]+(-[А-ЯЁ][а-яё]+)?$")


def looks_like_passport(u: User) -> bool:
    first, last = (u.first_name or "").strip(), (u.last_name or "").strip()
    return (
        bool(PASSPORT_WORD.match(first))
        and bool(PASSPORT_WORD.match(last))
        and first != last
    )


def label(u: User) -> str:
    return f"{html.escape(u.name)} (id {u.id})"


async def main() -> None:
    now = PRETEND_NOW or datetime.now(timezone.utc)
    today = now.date()
    if today > END_DATE:
        print(f"кампания закончилась {END_DATE}, ничего не делаю")
        return
    db = SessionLocal()
    fixed, changed_bad, reminded, waiting, exhausted, unreachable = [], [], [], [], [], []
    try:
        for uid, baseline in BASELINE.items():
            u = db.get(User, uid)
            if u is None or not u.is_active or u.deleted_at or u.archived_at:
                print(f"{uid}: не активен, пропускаю")
                continue
            if u.name != baseline:
                (fixed if looks_like_passport(u) else changed_bad).append(u)
                continue
            sent = (
                db.query(Notification)
                .filter(
                    Notification.user_id == uid,
                    Notification.title.in_([FIRST_TITLE, REMINDER_TITLE]),
                    Notification.created_at >= CAMPAIGN_START,
                )
                .order_by(Notification.created_at.desc())
                .all()
            )
            reminders = sum(1 for n in sent if n.title == REMINDER_TITLE)
            last_at = sent[0].created_at if sent else None
            if last_at is not None and last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=timezone.utc)
            if reminders >= MAX_REMINDERS or today == END_DATE:
                exhausted.append(u)
                continue
            if last_at is not None and now - last_at < MIN_GAP:
                waiting.append(u)
                continue
            if not (u.telegram_chat_id and u.telegram_notifications_enabled):
                unreachable.append(u)
            reminded.append(u)
            print(f"{uid}: напоминание №{reminders + 1}{' (dry-run)' if DRY_RUN else ''}")
            if DRY_RUN:
                continue
            body = REMINDER_BODY.format(name=u.name)
            db.add(Notification(user_id=uid, title=REMINDER_TITLE, text=body))
            db.commit()
            if u.telegram_chat_id and u.telegram_notifications_enabled:
                # parse_mode=HTML: имя теперь правит сам ученик, `<` или `&`
                # в нём сорвали бы отправку. In-app текст шаблоны экранируют сами.
                ok = await tg.send_message(
                    u.telegram_chat_id,
                    f"{REMINDER_TITLE}\n\n{REMINDER_BODY.format(name=html.escape(u.name))}",
                )
                print(f"{uid}: telegram {'доставлено' if ok else 'НЕ доставлено'}")
                if not ok:
                    unreachable.append(u)

        final = today == END_DATE
        print(
            f"исправили {len(fixed)}, сменили не по паспорту {len(changed_bad)}, "
            f"напомнил {len(reminded)}, ждут паузы {len(waiting)}, "
            f"напоминания кончились {len(exhausted)}"
        )
        if not (reminded or final):
            return

        def block(title: str, users: list[User]) -> str:
            if not users:
                return ""
            return f"\n\n<b>{title} ({len(users)}):</b>\n" + "\n".join(label(x) for x in users)

        head = (
            "<b>Имена учеников: итог кампании</b>\nНапоминаний больше не будет."
            if final
            else f"<b>Имена учеников, {today:%d.%m}</b>"
        )
        not_fixed = reminded + waiting + exhausted
        report = (
            head
            + block("Исправили", fixed)
            + block("Сменили, но не похоже на паспорт", changed_bad)
            + (block("Напомнил сегодня", reminded) if not final else "")
            + (block("Так и не исправили", not_fixed) if final else "")
            + block("Не дошло в Telegram", unreachable)
        )
        if final:
            report += "\n\nЗадание можно снять: rm /etc/cron.d/portfolio-name-reminder"
        owner = db.get(User, OWNER_ID)
        if DRY_RUN:
            print("--- отчёт владельцу (dry-run, не отправлен) ---\n" + report)
        elif owner and owner.telegram_chat_id:
            ok = await tg.send_message(owner.telegram_chat_id, report)
            print(f"отчёт владельцу: {'доставлен' if ok else 'НЕ доставлен'}")
    finally:
        db.close()
        await tg.close_client()


asyncio.run(main())
