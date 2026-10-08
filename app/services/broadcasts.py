"""Рассылки ученикам через бота — данные, получатели, проверка, журнал.

План — `plans/2026-10-07-apparchi-сообщения-лизы-я-с-вами.md`. Сеть — в
`services/broadcast_delivery.py`, здесь только база и правила.

**Кому.** Та же тройка, что у блока задания, и то же правило —
`task_blocks.is_block_open_to`: тариф и уровень сужают друг друга, ученик
поимённо получает при любом тарифе и уровне. Два отличия от блока, оба
намеренные:

- **пустой выбор не отправляется.** У блока «ничего не отмечено» значит
  «видят все», у рассылки это была бы случайная рассылка всей школе;
- **служебные аккаунты** (`REPORT_EXCLUDED_USER_IDS`) по тарифу и уровню не
  попадают, поимённо — попадают: так преподаватель проверяет рассылку на
  «службе заботы», как тестовые недели (`docs/инструкция-конструктор-недели.md`).

Не получают никак: заблокированные, архив, удалённые и ученики с закрытым по
сроку кабинетом (`access_state.access_expired` — то же правило, что у входа и
напоминаний).

**Проверка у преподавателя.** Отправить можно только ту версию, которую
преподаватель видел у себя в Telegram: `fingerprint` берёт текст, вложение и
выбор получателей, любая правка его меняет и требует новой проверки.

Ответы службы заботы (владелец 08.10.2026, вопросы в плане): выключенные
уведомления — в Telegram не шлём (`RECIPIENT_NOTIFICATIONS_OFF`); учеников без
уровня нет; копия — в колокольчик сайта каждому получателю, в том числе без
бота и с выключенными уведомлениями (`create_bells`); отправка — сразу или в
назначенное время: по Москве всем разом или по часам каждого ученика.

**Отложенная отправка.** «Запланировать» фиксирует проверенную версию
(статус `scheduled`, править нельзя — только отменить). В `fire_at`
планировщик (`exam_scheduler._run_broadcast_schedule`, раз в минуту) заводит
журнал: у каждого ученика свой `due_at`, и проход берёт только тех, чьё время
пришло. При «у ученика» `fire_at` — момент самого раннего пояса, отправка идёт
волнами по часу, пока не дойдёт до последнего. Пояс не указан — по Москве.
"""
from __future__ import annotations

import hashlib
import html
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from sqlalchemy import or_, update
from sqlalchemy.orm import Session as DBSession

from app.constants import BROADCAST_PREVIEW_FALLBACK_IDS, REPORT_EXCLUDED_USER_IDS, TARIFFS, TIMEZONES
from app.models.broadcast import (
    MEDIA_PHOTO,
    MEDIA_VIDEO_NOTE,
    MEDIA_VOICE,
    RECIPIENT_FAILED,
    RECIPIENT_NO_TELEGRAM,
    RECIPIENT_NOTIFICATIONS_OFF,
    RECIPIENT_PENDING,
    RECIPIENT_SENT,
    SCHEDULE_LOCAL,
    SCHEDULE_MSK,
    STATUS_DRAFT,
    STATUS_SCHEDULED,
    STATUS_SENDING,
    STATUS_SENT,
    Broadcast,
    BroadcastLevel,
    BroadcastRecipient,
    BroadcastStudent,
    BroadcastTariff,
)
from app.models.notification import Notification
from app.models.role import Role
from app.models.user import User
from app.services.access_state import access_expired
from app.services.task_blocks import BlockAudience, BlockViewer, is_block_open_to
from app.services.tz import MSK_TZ, msk_text

LEVELS = (1, 2)

# Лимиты Telegram: текст сообщения и подпись к фото или голосовому. Считает
# Telegram в UTF-16 и без разметки — так же считает `visible_length`. У
# кружка подписи нет вовсе: текст уходит следом отдельным сообщением.
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024

# «Дослать» пускает новый проход, только если прежний молчит столько времени:
# иначе кнопка, нажатая во время живой отправки, удвоила бы поток.
RESUME_AFTER = timedelta(minutes=10)

RECIPIENT_LABELS = {
    RECIPIENT_PENDING: "Ждёт отправки",
    RECIPIENT_SENT: "Доставлено",
    RECIPIENT_FAILED: "Не дошло",
    RECIPIENT_NO_TELEGRAM: "Бот не подключён",
    RECIPIENT_NOTIFICATIONS_OFF: "Уведомления выключены",
}
MEDIA_LABELS = {
    MEDIA_PHOTO: "Фото",
    MEDIA_VOICE: "Голосовое",
    MEDIA_VIDEO_NOTE: "Кружок",
}
SCHEDULE_LABELS = {
    SCHEDULE_MSK: "по Москве",
    SCHEDULE_LOCAL: "по часам ученика",
}

# Копия в колокольчике. Сотрудник для ученика — только «преподаватель»
# (`docs/invariants/feedback.md`): ни имени, ни должности.
BELL_TITLE = "Сообщение от преподавателя"
BELL_MEDIA_TEXT = {
    MEDIA_PHOTO: "Фото",
    MEDIA_VOICE: "Голосовое сообщение",
    MEDIA_VIDEO_NOTE: "Видеосообщение",
}


# ── Разметка Telegram ───────────────────────────────────────────────────────
#
# Редактор на экране (`static/js/tg-text-field.js`) отдаёт HTML из
# contenteditable: <strong>, <div>, <br>, <span style=…> — что угодно, что
# написал браузер. Telegram принимает узкое подмножество
# (core.telegram.org/bots/api#html-style), и одна лишняя метка роняет всю
# отправку. Поэтому в базу кладётся только разметка Telegram: известные теги
# переводятся, остальные снимаются с сохранением текста, атрибуты — только
# `href` с http(s). Переносы — символом `\n`, как их понимает Telegram.

_INLINE_TAGS = {
    "b": "b", "strong": "b",
    "i": "i", "em": "i",
    "u": "u", "ins": "u",
    "s": "s", "strike": "s", "del": "s",
    "tg-spoiler": "tg-spoiler",
}
_BLOCK_BREAK_TAGS = {"div", "p", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"script", "style"}
_SAFE_LINK_RE = re.compile(r"^https?://", re.IGNORECASE)


class _TelegramHtmlCleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        # Открытые теги Telegram в выходе (имя). Закрываются строго в
        # обратном порядке: Telegram не принимает перекрывающихся меток.
        self.stack: list[str] = []
        # Для каждого входного тега — что он открыл в выходе (или None), чтобы
        # закрывающий тег знал, что закрывать.
        self.opened: list[tuple[str, str | None]] = []
        # Внутри <script>/<style> текст не показывается и в сообщение не идёт.
        self.skip_depth = 0

    # Строка выхода без разметки — чтобы понять, стоим ли мы в начале строки.
    def _plain_tail(self) -> str:
        return re.sub(r"<[^>]+>", "", "".join(self.out))

    def _newline(self) -> None:
        tail = self._plain_tail()
        if tail and not tail.endswith("\n"):
            self.out.append("\n")

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attrs = dict(attrs)
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "br":
            self.out.append("\n")
            return
        if tag in _BLOCK_BREAK_TAGS:
            self._newline()
            if tag == "li":
                self.out.append("• ")
            self.opened.append((tag, None))
            return
        target: str | None = None
        if tag in _INLINE_TAGS:
            target = _INLINE_TAGS[tag]
        elif tag == "span" and "tg-spoiler" in (attrs.get("class") or "").split():
            target = "tg-spoiler"
        elif tag == "blockquote" and "blockquote" not in self.stack:
            self._newline()
            target = "blockquote"
        elif tag == "a":
            href = (attrs.get("href") or "").strip()
            if _SAFE_LINK_RE.match(href) and "a" not in self.stack:
                self.out.append(f'<a href="{html.escape(href, quote=True)}">')
                self.stack.append("a")
                self.opened.append((tag, "a"))
                return
        if target is not None and target in self.stack:
            target = None  # вложенный жирный в жирном Telegram не нужен
        if target is not None:
            self.out.append(f"<{target}>")
            self.stack.append(target)
        self.opened.append((tag, target))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "br":
            return
        for index in range(len(self.opened) - 1, -1, -1):
            name, target = self.opened[index]
            if name != tag:
                continue
            del self.opened[index]
            if target is not None and target in self.stack:
                # Закрыть всё, что открыто поверх, — перекрытия Telegram не
                # примет; потерянное оформление хвоста лучше отказа отправки.
                while self.stack:
                    closing = self.stack.pop()
                    self.out.append(f"</{closing}>")
                    if closing == target:
                        break
                self.opened = [
                    (n, t) for n, t in self.opened if t is None or t in self.stack
                ]
            if tag == "blockquote" or tag in _BLOCK_BREAK_TAGS:
                self._newline()
            return

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        self.out.append(html.escape(data.replace("\xa0", " "), quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        text = "".join(self.out)
        # Пустые метки (`<b></b>`) Telegram отвергает.
        previous = None
        while previous != text:
            previous = text
            text = re.sub(r"<(b|i|u|s|tg-spoiler|blockquote)>(\s*)</\1>", r"\2", text)
            text = re.sub(r'<a href="[^"]*">(\s*)</a>', r"\1", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def clean_telegram_html(raw: str | None) -> str:
    """HTML из редактора → разметка, которую примет Telegram. Повторный прогон
    по уже очищенному ничего не меняет."""
    if not raw:
        return ""
    cleaner = _TelegramHtmlCleaner()
    cleaner.feed(raw.replace("\r\n", "\n"))
    cleaner.close()
    return cleaner.result()


def visible_length(telegram_html: str) -> int:
    """Длина текста так, как её считает Telegram: без разметки, в UTF-16."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", telegram_html or ""))
    return len(plain.encode("utf-16-le")) // 2


def telegram_html_for_page(telegram_html: str, *, student: bool = False) -> str:
    """Разметку Telegram показать на странице — в редакторе и в просмотре.

    Безопасно: в базе лежит только очищенное (`clean_telegram_html`), а здесь
    меняется лишь вид спойлера (браузер не знает `<tg-spoiler>`) и переносы.
    `student` — колокольчик ученика: спойлер скрыт и открывается нажатием
    (фокус — `tabindex`, стиль `.bc-preview--student` в broadcasts.css).
    """
    spoiler = '<span class="tg-spoiler" tabindex="0">' if student else '<span class="tg-spoiler">'
    shown = (telegram_html or "").replace("<tg-spoiler>", spoiler)
    shown = shown.replace("</tg-spoiler>", "</span>")
    shown = shown.replace('<a href="', '<a target="_blank" rel="noopener noreferrer" href="')
    # Цитата на странице — блок сама, перенос вокруг неё дал бы пустую строку,
    # которой в Telegram нет. Обратно чистка вернёт его (`_newline`).
    shown = shown.replace("</blockquote>\n", "</blockquote>").replace("\n<blockquote>", "<blockquote>")
    return shown.replace("\n", "<br>")


def plain_text(telegram_html: str) -> str:
    """Текст без разметки — для попапа колокольчика."""
    return html.unescape(re.sub(r"<[^>]+>", "", telegram_html or "")).strip()


def text_limit(media_kind: str | None) -> int:
    """Сколько символов можно в тексте при этом вложении."""
    if media_kind in (MEDIA_PHOTO, MEDIA_VOICE):
        return CAPTION_LIMIT
    return TEXT_LIMIT


def text_problem(broadcast: Broadcast) -> str | None:
    """Почему текст не уйдёт в Telegram, или None.

    С 08.10.2026 черновик сохраняется и с длинным текстом — чтобы ошибка не
    стирала набранное, — поэтому лимит держат проверка и отправка, а не только
    сохранение."""
    limit = text_limit(broadcast.media_kind)
    length = visible_length(broadcast.text or "")
    if length <= limit:
        return None
    hint = " (подпись к фото и голосовому — до 1024)" if limit == CAPTION_LIMIT else ""
    return f"Текст длиннее {limit} символов: сейчас {length}{hint}"


# ── Выбор получателей ───────────────────────────────────────────────────────


def get_audience(db: DBSession, broadcast_id: int) -> BlockAudience:
    tariffs = {
        row[0] for row in
        db.query(BroadcastTariff.tariff).filter(BroadcastTariff.broadcast_id == broadcast_id)
    }
    levels = {
        row[0] for row in
        db.query(BroadcastLevel.level).filter(BroadcastLevel.broadcast_id == broadcast_id)
    }
    user_ids = {
        row[0] for row in
        db.query(BroadcastStudent.user_id).filter(BroadcastStudent.broadcast_id == broadcast_id)
    }
    return BlockAudience(
        tariffs=frozenset(tariffs), levels=frozenset(levels), user_ids=frozenset(user_ids),
    )


def normalize_audience(
    db: DBSession, *, tariffs: list[str], levels: list, user_ids: list,
) -> BlockAudience:
    """Выбор с формы → проверенные значения. Чужое отбрасывается молча: тариф
    не из `TARIFFS`, уровень не 1/2, id не действующего ученика."""
    clean_tariffs = frozenset(t for t in tariffs if t in TARIFFS)
    clean_levels: set[int] = set()
    for level in levels:
        try:
            value = int(level)
        except (TypeError, ValueError):
            continue
        if value in LEVELS:
            clean_levels.add(value)
    wanted: set[int] = set()
    for user_id in user_ids:
        try:
            wanted.add(int(user_id))
        except (TypeError, ValueError):
            continue
    clean_users: frozenset[int] = frozenset()
    if wanted:
        clean_users = frozenset(
            row[0] for row in _students(db).with_entities(User.id).filter(User.id.in_(wanted))
        )
    return BlockAudience(tariffs=clean_tariffs, levels=frozenset(clean_levels), user_ids=clean_users)


def save_audience(db: DBSession, broadcast: Broadcast, audience: BlockAudience) -> None:
    for model in (BroadcastTariff, BroadcastLevel, BroadcastStudent):
        db.query(model).filter(model.broadcast_id == broadcast.id).delete(synchronize_session=False)
    for tariff in sorted(audience.tariffs):
        db.add(BroadcastTariff(broadcast_id=broadcast.id, tariff=tariff))
    for level in sorted(audience.levels):
        db.add(BroadcastLevel(broadcast_id=broadcast.id, level=level))
    for user_id in sorted(audience.user_ids):
        db.add(BroadcastStudent(broadcast_id=broadcast.id, user_id=user_id))


def _students(db: DBSession):
    """Действующие ученики: ранг 1, не заблокированы, не удалены, не в архиве."""
    return (
        db.query(User)
        .join(Role, User.role_id == Role.id)
        .filter(
            Role.rank == 1,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
        )
    )


def audience_users(db: DBSession, audience: BlockAudience) -> list[User]:
    """Кто получит рассылку при таком выборе. Пустой выбор — никто."""
    if audience.is_everyone:
        return []
    now = datetime.now(timezone.utc)
    chosen: list[User] = []
    for user in _students(db).order_by(User.last_name, User.first_name, User.id):
        if access_expired(user, now):
            continue
        named = user.id in audience.user_ids
        if not named and user.id in REPORT_EXCLUDED_USER_IDS:
            continue
        viewer = BlockViewer(db, user_id=user.id, tariff=user.tariff)
        if is_block_open_to(audience, viewer):
            chosen.append(user)
    return chosen


_TIMEZONE_CODES = frozenset(code for code, _ in TIMEZONES)


def has_timezone(user: User) -> bool:
    return (user.timezone or "").strip() in _TIMEZONE_CODES


def tz_offset(user: User) -> int:
    """Пояс ученика из анкеты — часы от МСК. Не указан или не из списка — 0."""
    if not has_timezone(user):
        return 0
    return int(user.timezone.strip())


def _aware(value: datetime) -> datetime:
    """SQLite в тестах отдаёт время без пояса — в базе всегда UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def due_moment(broadcast: Broadcast, user: User) -> datetime | None:
    """Когда ученику уйдёт сообщение. «У ученика» — то же время на его часах:
    10:00 у МСК+4 — это 06:00 по Москве. Без расписания — сразу (None)."""
    if not broadcast.schedule_mode or broadcast.scheduled_at is None:
        return None
    moment = _aware(broadcast.scheduled_at)
    if broadcast.schedule_mode == SCHEDULE_LOCAL:
        moment -= timedelta(hours=tz_offset(user))
    return moment


def schedule_text(broadcast: Broadcast) -> str:
    """Когда уйдёт — словами: «10.10.2026 в 10:00 по Москве»."""
    if not broadcast.schedule_mode or broadcast.scheduled_at is None:
        return "сразу"
    return f"{msk_text(broadcast.scheduled_at)} {SCHEDULE_LABELS[broadcast.schedule_mode]}"


def schedule_input_value(broadcast: Broadcast) -> str:
    """Выбранное время для поля `datetime-local` — как его вводили."""
    if broadcast.scheduled_at is None:
        return ""
    return _aware(broadcast.scheduled_at).astimezone(MSK_TZ).strftime("%Y-%m-%dT%H:%M")


def schedule_problem(broadcast: Broadcast, users: list[User], now: datetime | None = None) -> str | None:
    """Почему это время не годится, или None. Время должно быть впереди у
    всех: при «у ученика» раньше всех оно наступит у самого восточного пояса."""
    if not broadcast.schedule_mode:
        return None
    if broadcast.scheduled_at is None:
        return "Не указано время отправки"
    now = now or datetime.now(timezone.utc)
    first = min((due_moment(broadcast, user) for user in users), default=None)
    if first is None or first > now:
        return None
    if first != _aware(broadcast.scheduled_at):
        return "У части учеников это время по их часам уже прошло — выберите позже"
    return "Это время уже прошло — выберите позже"


def delivery_status(user: User) -> str:
    """С каким статусом ученик встанет в журнал в момент отправки."""
    if not user.telegram_chat_id:
        return RECIPIENT_NO_TELEGRAM
    if not user.telegram_notifications_enabled:
        return RECIPIENT_NOTIFICATIONS_OFF
    return RECIPIENT_PENDING


@dataclass(frozen=True)
class AudienceSummary:
    total: int
    reachable: int
    no_telegram: int
    notifications_off: int


def summarize(users: list[User]) -> AudienceSummary:
    statuses = [delivery_status(user) for user in users]
    return AudienceSummary(
        total=len(users),
        reachable=statuses.count(RECIPIENT_PENDING),
        no_telegram=statuses.count(RECIPIENT_NO_TELEGRAM),
        notifications_off=statuses.count(RECIPIENT_NOTIFICATIONS_OFF),
    )


def audience_text(audience: BlockAudience, names: dict[int, str] | None = None) -> str:
    """Выбор словами — для проверки у преподавателя и журнала."""
    parts: list[str] = []
    if audience.tariffs:
        parts.append("тариф " + ", ".join(sorted(audience.tariffs)))
    if audience.levels:
        parts.append("уровень " + ", ".join(str(level) for level in sorted(audience.levels)))
    narrowed = " и ".join(parts)
    if audience.user_ids:
        count = len(audience.user_ids)
        listed = ""
        if names:
            shown = [names[uid] for uid in sorted(audience.user_ids) if uid in names][:5]
            listed = ": " + ", ".join(shown) + ("…" if count > len(shown) else "")
        named = f"поимённо {count}{listed}"
        return f"{narrowed}; плюс {named}" if narrowed else named
    return narrowed or "никого"


def student_choices(db: DBSession) -> list[dict]:
    """Ученики для поиска «поимённо» — тот же вид, что у «Доступности блока»
    (`task_blocks.block_student_choices`), но только те, кому рассылка может
    уйти: без заблокированных и закрытых по сроку."""
    now = datetime.now(timezone.utc)
    rows = []
    for user in _students(db):
        if access_expired(user, now):
            continue
        name = " ".join(part for part in (user.last_name, user.first_name) if part).strip()
        rows.append({
            "id": user.id,
            "name": name or f"Ученик {user.id}",
            "username": (user.tg_username or "").strip().lstrip("@"),
            "tariff": user.tariff or "",
        })
    return sorted(rows, key=lambda row: (row["name"].lower(), row["id"]))


# ── Проверка у преподавателя ────────────────────────────────────────────────


def fingerprint(broadcast: Broadcast, audience: BlockAudience) -> str:
    """Отпечаток версии: текст, вложение, выбор получателей и когда отправить.
    Время — тоже часть проверенного: кнопка под проверкой в Telegram
    «Отправить … 10.10 в 10:00» должна значить ровно это."""
    parts = [
        broadcast.text or "",
        broadcast.media_kind or "",
        broadcast.media_s3_path or "",
        ",".join(sorted(audience.tariffs)),
        ",".join(str(level) for level in sorted(audience.levels)),
        ",".join(str(uid) for uid in sorted(audience.user_ids)),
        broadcast.schedule_mode or "",
        _aware(broadcast.scheduled_at).isoformat() if broadcast.scheduled_at else "",
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def preview_is_current(db: DBSession, broadcast: Broadcast) -> bool:
    """Видел ли преподаватель именно эту версию."""
    return bool(broadcast.preview_fingerprint) and (
        broadcast.preview_fingerprint == fingerprint(broadcast, get_audience(db, broadcast.id))
    )


def preview_choices(db: DBSession, sender_id: int) -> list[dict]:
    """Куда можно прислать проверку: свой Telegram и служебные аккаунты с ним.

    Служебные — не ученики (`REPORT_EXCLUDED_USER_IDS`): на них привязаны
    рабочие Telegram команды — Лизы («служба заботы», 277), владельца (199).
    У аккаунта ГП Лизы (id 10) и суперадмина (id 3) Telegram нет, а проверку
    владелец смотрит у себя, не у Лизы (владелец 08.10.2026). Первый пункт —
    выбор по умолчанию: свой Telegram, иначе запасной
    `BROADCAST_PREVIEW_FALLBACK_IDS`.
    """
    choices: list[dict] = []
    sender = db.get(User, sender_id)
    if sender is not None and sender.telegram_chat_id:
        choices.append({"id": sender.id, "label": "Мой Telegram", "chat_id": sender.telegram_chat_id})
    preferred = sorted(BROADCAST_PREVIEW_FALLBACK_IDS)
    others = sorted((REPORT_EXCLUDED_USER_IDS | BROADCAST_PREVIEW_FALLBACK_IDS) - set(preferred))
    for user_id in preferred + others:
        if user_id == sender_id:
            continue
        user = db.get(User, user_id)
        if user is None or not user.telegram_chat_id:
            continue
        name = " ".join(p for p in (user.first_name, user.last_name) if p) or user.name or "аккаунт"
        choices.append({
            "id": user.id,
            "label": f"{name} (служебный, id {user.id})",
            "chat_id": user.telegram_chat_id,
        })
    return choices


def preview_target(
    db: DBSession, sender_id: int, chosen_id: int | None = None,
) -> tuple[int, int] | None:
    """Куда прислать проверку: (id аккаунта, chat_id). Выбранный на форме —
    если он среди `preview_choices`, иначе первый из них."""
    choices = preview_choices(db, sender_id)
    for choice in choices:
        if chosen_id is not None and choice["id"] == chosen_id:
            return choice["id"], choice["chat_id"]
    if choices:
        return choices[0]["id"], choices[0]["chat_id"]
    return None


def new_preview_token() -> str:
    return secrets.token_urlsafe(16)


def invalidate_preview(broadcast: Broadcast) -> None:
    """Правка черновика — прежняя проверка больше не считается."""
    broadcast.preview_fingerprint = None
    broadcast.preview_token = None
    broadcast.telegram_file_id = None if broadcast.media_kind else broadcast.telegram_file_id


# ── Отправка: переходы состояния ────────────────────────────────────────────


class SendRefused(Exception):
    """Отправить нельзя — текст причины для человека."""


def _ready(db: DBSession, broadcast_id: int) -> tuple[Broadcast, list[User]]:
    """Проверенная версия, которую можно отправить, и кому она уйдёт."""
    broadcast = db.get(Broadcast, broadcast_id)
    if broadcast is None:
        raise SendRefused("Рассылки нет")
    if broadcast.status == STATUS_SCHEDULED:
        raise SendRefused("Рассылка уже запланирована")
    if broadcast.status != STATUS_DRAFT:
        raise SendRefused("Эта рассылка уже отправлена")
    audience = get_audience(db, broadcast.id)
    if audience.is_everyone:
        raise SendRefused("Не выбрано, кому отправить")
    if not (broadcast.text or broadcast.media_kind):
        raise SendRefused("Сообщение пустое")
    problem = text_problem(broadcast)
    if problem:
        raise SendRefused(problem)
    if not preview_is_current(db, broadcast):
        raise SendRefused("Сначала пришлите себе на проверку эту версию сообщения")
    if broadcast.media_kind and not broadcast.telegram_file_id:
        raise SendRefused("Вложение не дошло до Telegram на проверке — пришлите проверку заново")
    users = audience_users(db, audience)
    if not users:
        raise SendRefused("Под этот выбор не подходит ни один ученик")
    return broadcast, users


def _journal(db: DBSession, broadcast: Broadcast, users: list[User]) -> int:
    """Журнал по ученикам — список с этой секунды не меняется. Возвращает,
    скольким уйдёт в Telegram."""
    reachable = 0
    for user in users:
        status = delivery_status(user)
        reachable += status == RECIPIENT_PENDING
        db.add(BroadcastRecipient(
            broadcast_id=broadcast.id, user_id=user.id, status=status,
            due_at=due_moment(broadcast, user),
        ))
    return reachable


def approve(db: DBSession, broadcast_id: int, *, actor_id: int) -> tuple[bool, int]:
    """«Отправить» на сайте или под проверкой в Telegram. Без расписания —
    в отправку сразу, с расписанием — запланировать. Возвращает (запускать ли
    проход сейчас, скольким уйдёт в Telegram)."""
    broadcast = db.get(Broadcast, broadcast_id)
    if broadcast is not None and broadcast.schedule_mode:
        return False, schedule_sending(db, broadcast_id, actor_id=actor_id)
    return True, start_sending(db, broadcast_id, actor_id=actor_id)


def start_sending(db: DBSession, broadcast_id: int, *, actor_id: int) -> int:
    """Взять рассылку в отправку: черновик → «отправляется», журнал по ученикам.

    Переход — условным UPDATE по статусу: двойное нажатие или кнопка в
    Telegram вместе с кнопкой на сайте возьмут рассылку один раз. Список
    получателей фиксируется здесь и дальше не меняется. Возвращает, скольким
    уйдёт сообщение; коммитит сам.
    """
    broadcast, users = _ready(db, broadcast_id)
    if broadcast.schedule_mode:
        raise SendRefused("У рассылки назначено время — её можно только запланировать")
    now = datetime.now(timezone.utc)
    taken = db.execute(
        update(Broadcast)
        .where(Broadcast.id == broadcast.id, Broadcast.status == STATUS_DRAFT)
        .values(status=STATUS_SENDING, sent_by_id=actor_id, sending_started_at=now, updated_at=now)
    ).rowcount
    if taken != 1:
        db.rollback()
        raise SendRefused("Эта рассылка уже отправлена")
    reachable = _journal(db, broadcast, users)
    db.commit()
    return reachable


def schedule_sending(db: DBSession, broadcast_id: int, *, actor_id: int) -> int:
    """Запланировать: черновик → «запланирована». Журнала ещё нет — его
    заведёт `fire_scheduled` в `fire_at`. Возвращает, скольким уйдёт в
    Telegram на сегодня; коммитит сам."""
    broadcast, users = _ready(db, broadcast_id)
    if not broadcast.schedule_mode:
        raise SendRefused("Не выбрано время отправки")
    problem = schedule_problem(broadcast, users)
    if problem:
        raise SendRefused(problem)
    fire_at = min(due_moment(broadcast, user) for user in users)
    now = datetime.now(timezone.utc)
    taken = db.execute(
        update(Broadcast)
        .where(Broadcast.id == broadcast.id, Broadcast.status == STATUS_DRAFT)
        .values(status=STATUS_SCHEDULED, sent_by_id=actor_id, fire_at=fire_at, updated_at=now)
    ).rowcount
    if taken != 1:
        db.rollback()
        raise SendRefused("Эта рассылка уже отправлена")
    db.commit()
    return sum(delivery_status(user) == RECIPIENT_PENDING for user in users)


def cancel_schedule(db: DBSession, broadcast_id: int) -> bool:
    """«Отменить отправку»: запланированная → черновик, пока планировщик её не
    взял. Проверка остаётся в силе — версия та же. Коммитит сам."""
    now = datetime.now(timezone.utc)
    taken = db.execute(
        update(Broadcast)
        .where(Broadcast.id == broadcast_id, Broadcast.status == STATUS_SCHEDULED)
        .values(status=STATUS_DRAFT, sent_by_id=None, fire_at=None, updated_at=now)
    ).rowcount
    db.commit()
    return taken == 1


def fire_scheduled(db: DBSession, now: datetime | None = None) -> list[int]:
    """Запланированные, чьё время пришло: «отправляется» и журнал по ученикам
    с их `due_at`. Проход взят сразу (`sending_started_at`). Получатели — на
    этот момент: кто успел уйти в архив, тот не получит. Коммитит сам."""
    now = now or datetime.now(timezone.utc)
    fired: list[int] = []
    ids = [
        row[0] for row in db.query(Broadcast.id).filter(
            Broadcast.status == STATUS_SCHEDULED, Broadcast.fire_at <= now,
        )
    ]
    for broadcast_id in ids:
        taken = db.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id, Broadcast.status == STATUS_SCHEDULED)
            .values(status=STATUS_SENDING, sending_started_at=now, updated_at=now)
        ).rowcount
        if taken != 1:
            db.rollback()
            continue
        broadcast = db.get(Broadcast, broadcast_id)
        _journal(db, broadcast, audience_users(db, get_audience(db, broadcast_id)))
        db.commit()
        fired.append(broadcast_id)
    return fired


def _due_work(db: DBSession, broadcast_id: int, now: datetime):
    """Строки журнала, чьё время пришло и где ещё есть что делать: Telegram
    ждёт или копии в колокольчике нет."""
    return db.query(BroadcastRecipient).filter(
        BroadcastRecipient.broadcast_id == broadcast_id,
        or_(BroadcastRecipient.due_at.is_(None), BroadcastRecipient.due_at <= now),
        or_(BroadcastRecipient.status == RECIPIENT_PENDING, BroadcastRecipient.bell_at.is_(None)),
    )


def _lease_free(now: datetime):
    """Прохода нет: прежний закончился или молчит `RESUME_AFTER` (умер)."""
    return or_(
        Broadcast.sending_started_at.is_(None),
        Broadcast.sending_started_at < now - RESUME_AFTER,
    )


def claim_due_runs(db: DBSession, now: datetime | None = None) -> list[int]:
    """Запланированные в отправке, у кого подошла следующая волна (или прежний
    проход умер): взять проход. Коммитит сам."""
    now = now or datetime.now(timezone.utc)
    claimed: list[int] = []
    ids = [
        row[0] for row in db.query(Broadcast.id).filter(
            Broadcast.status == STATUS_SENDING,
            Broadcast.schedule_mode.isnot(None),
            _lease_free(now),
        )
    ]
    for broadcast_id in ids:
        if _due_work(db, broadcast_id, now).first() is None:
            continue
        taken = db.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id, Broadcast.status == STATUS_SENDING, _lease_free(now))
            .values(sending_started_at=now, updated_at=now)
        ).rowcount
        db.commit()
        if taken == 1:
            claimed.append(broadcast_id)
    return claimed


def create_bells(db: DBSession, broadcast_id: int, now: datetime | None = None) -> list[int]:
    """Копия в колокольчике всем, чьё время пришло, — один раз на ученика
    (`bell_at`). Без доставки в Telegram: туда уходит сама рассылка. Не
    коммитит; возвращает, кому завели (сбросить счётчик непрочитанных)."""
    now = now or datetime.now(timezone.utc)
    broadcast = db.get(Broadcast, broadcast_id)
    if broadcast is None:
        return []
    text = plain_text(broadcast.text) or BELL_MEDIA_TEXT.get(broadcast.media_kind or "", "")
    rows = (
        _due_work(db, broadcast_id, now)
        .filter(BroadcastRecipient.bell_at.is_(None))
        .all()
    )
    for row in rows:
        db.add(Notification(
            user_id=row.user_id, title=BELL_TITLE, text=text or None,
            broadcast_id=broadcast_id, created_at=now,
        ))
        row.bell_at = now
    return [row.user_id for row in rows]


def claim_resume(db: DBSession, broadcast_id: int) -> bool:
    """«Дослать»: взять зависшую отправку, если прежний проход молчит
    `RESUME_AFTER`. Коммитит сам."""
    now = datetime.now(timezone.utc)
    taken = db.execute(
        update(Broadcast)
        .where(
            Broadcast.id == broadcast_id,
            Broadcast.status == STATUS_SENDING,
            _lease_free(now),
        )
        .values(sending_started_at=now, updated_at=now)
    ).rowcount
    db.commit()
    return taken == 1


def pass_running(broadcast: Broadcast, now: datetime | None = None) -> bool:
    """Проход идёт прямо сейчас — живой, не умерший."""
    if broadcast.status != STATUS_SENDING or broadcast.sending_started_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return _aware(broadcast.sending_started_at) >= now - RESUME_AFTER


def can_resume(db: DBSession, broadcast: Broadcast, now: datetime | None = None) -> bool:
    """Показать «Дослать»: прохода нет, а работа, чьё время пришло, осталась.
    Следующие волны «у ученика» сюда не попадают — их время не пришло."""
    if broadcast.status != STATUS_SENDING or pass_running(broadcast, now):
        return False
    now = now or datetime.now(timezone.utc)
    return _due_work(db, broadcast.id, now).first() is not None


def finish_if_done(db: DBSession, broadcast_id: int) -> None:
    """Конец прохода. Ничего не осталось — рассылка отправлена; осталось
    (следующие волны «у ученика») — проход отпускается, следующую возьмёт
    планировщик. Коммитит сам."""
    left = (
        db.query(BroadcastRecipient.id)
        .filter(
            BroadcastRecipient.broadcast_id == broadcast_id,
            or_(BroadcastRecipient.status == RECIPIENT_PENDING, BroadcastRecipient.bell_at.is_(None)),
        )
        .first()
    )
    now = datetime.now(timezone.utc)
    values = {"sending_started_at": None, "updated_at": now}
    if left is None:
        values.update(status=STATUS_SENT, sent_at=now)
    db.execute(
        update(Broadcast)
        .where(Broadcast.id == broadcast_id, Broadcast.status == STATUS_SENDING)
        .values(**values)
    )
    db.commit()


def next_wave(db: DBSession, broadcast_id: int) -> datetime | None:
    """Когда следующая волна «у ученика» — для отчёта."""
    row = (
        db.query(BroadcastRecipient.due_at)
        .filter(
            BroadcastRecipient.broadcast_id == broadcast_id,
            BroadcastRecipient.due_at > datetime.now(timezone.utc),
            or_(BroadcastRecipient.status == RECIPIENT_PENDING, BroadcastRecipient.bell_at.is_(None)),
        )
        .order_by(BroadcastRecipient.due_at)
        .first()
    )
    return row[0] if row else None


def recipient_counts(db: DBSession, broadcast_id: int) -> dict[str, int]:
    counts = {status: 0 for status in RECIPIENT_LABELS}
    for status, in db.query(BroadcastRecipient.status).filter(
        BroadcastRecipient.broadcast_id == broadcast_id
    ):
        counts[status] = counts.get(status, 0) + 1
    return counts
