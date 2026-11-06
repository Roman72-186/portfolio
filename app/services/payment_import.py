"""Загрузка списка оплаты от заказчика: Excel «имя — фамилия — тариф — сумма».

Шаг 2 плана `plans/2026-09-30-apparchi-monthly-payments.md`. Заказчик
присылает, кто сколько платит в месяц; загрузка проставляет это ученикам.

Два прохода по одному файлу. Предпросмотр (`build_preview`) ничего не пишет:
находит ученика по имени и фамилии, сверяет тариф и показывает, что
поменяется. Запись (`apply_import`) разбирает файл заново — браузеру не
доверяем — и пишет только строки, которые человек отметил, с учеником,
которого он подтвердил или выбрал руками.

Писать — только через `payments.apply_payment_settings`, одну точку ручной
настройки оплаты (журнал, проверки, правило «оплачено по»). Окно и
оплаченный месяц файл не несёт. Их можно задать на всю загрузку
(`Defaults`), и тогда они заполнят только пустые поля: окно, поставленное
ученику в карточке, загрузка не перетирает.

Что делаем с суммой. Совпала с ценой справочника для тарифа ученика в одном
из наборов — ставим этот набор и снимаем свою цену: подорожание набора тогда
дойдёт до ученика правкой справочника. Не совпала ни с одним — записываем
своей ценой. Тариф загрузка не меняет: смена тарифа — `apply_tariff_change`
со своими правилами, а файл заказчика для неё не основание.

Новые ученики (владелец 26.10.2026: «два списка… там в списках будет
оплата… в какое окно их ставить… и после этой даты вставляется ссылка на
оплату»). В строке можно дать «Ник», «Окно» и «Первый месяц оплаты»: они
главнее общих значений формы и так же ложатся только в пустые поля, поэтому
тот же файл можно загрузить ещё раз — уже настроенных он не тронет. Первый
месяц — тот, за который ученик заплатит на платформе: оплаченным ставится
месяц перед ним, и «Оплатить» появится в первый день окна этого месяца.
Каникулярному тариф из файла пишется в «Тариф после оплаты»: своего тарифа у
него нет, а оплата месяца сама переведёт его в годовой курс
(`payments.record_paid` → `user_management.move_to_annual`) и поставит этот
тариф. Сумма у него всегда своя цена: цена по справочнику считается от
текущего тарифа, у каникулярного он пустой, и ссылку не из чего было бы
собрать.
"""
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session as DBSession

from app.constants import (
    PROGRAM_HOLIDAY, REPORT_EXCLUDED_USER_IDS, TARIFF_DISPLAY, TARIFFS, TARIFFS_CURRENT,
)
from app.models.payment import COHORT_LABELS, COHORTS
from app.models.role import Role
from app.models.user import User
from app.services import payments
from app.services.contacts import normalize_tg_username
from app.services.tz import MSK_TZ

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000
# Шапка может стоять не в первой строке: сверху бывает заголовок таблицы.
HEADER_SCAN_ROWS = 10

# Статус строки после предпросмотра.
ROW_READY = "ready"            # ученик найден, есть что записать
ROW_SAME = "same"              # ученик найден, у него уже всё так
ROW_TARIFF = "tariff"          # тариф в файле другой — запись только по галочке
ROW_NOT_FOUND = "not_found"    # такого имени нет — выбрать ученика руками
ROW_AMBIGUOUS = "ambiguous"    # несколько учеников с таким именем — выбрать руками
ROW_DUPLICATE = "duplicate"    # этот ученик уже встретился выше в файле
ROW_SKIPPED = "skipped"        # служебный аккаунт или архив — не пишем
ROW_ERROR = "error"            # строку не разобрать — не пишем

# Строки, которые без выбора человека не записываются.
NEEDS_PICK = (ROW_NOT_FOUND, ROW_AMBIGUOUS, ROW_DUPLICATE)


class ImportFileError(ValueError):
    """Файл целиком не годится — текст для показа человеку."""


@dataclass
class SheetRow:
    line: int                     # номер строки в Excel, как видит человек
    name_text: str                # имя как в файле
    tokens: frozenset[str]        # слова имени для сравнения
    tariff_text: str = ""
    tariff: str | None = None     # код тарифа или None, если колонки нет / пусто
    amount_kop: int | None = None
    error: str | None = None
    nick: str = ""                # ник Telegram без «@», в нижнем регистре
    window_start: int | None = None
    window_end: int | None = None
    first_month: date | None = None   # первый месяц оплаты на платформе

    @property
    def paid_through(self) -> date | None:
        """Оплаченным считается месяц перед первым месяцем оплаты."""
        return payments.add_months(self.first_month, -1) if self.first_month else None


@dataclass(frozen=True)
class Defaults:
    """Окно и оплаченный месяц на всю загрузку — только в пустые поля ученика."""
    window_start: int | None = None
    window_end: int | None = None
    paid_through: date | None = None


@dataclass
class Target:
    """Настройки оплаты, которые строка запишет ученику."""
    window_start: int | None
    window_end: int | None
    cohort: str
    price_kop: int | None
    paid_through: date | None
    note: str = ""
    # Тариф после оплаты — только у каникулярного; None — не трогать.
    next_tariff: str | None = None


@dataclass
class RowResult:
    row: SheetRow
    status: str
    student: User | None = None
    candidates: list[User] = field(default_factory=list)
    note: str = ""
    target: Target | None = None
    changes: list[str] = field(default_factory=list)


def parse_defaults(start: str, end: str, month: str) -> Defaults:
    """Общие значения с формы. Плохие — `payments.PaymentSettingsError`."""
    days = []
    for raw in (start, end):
        text = (raw or "").strip()
        if text and not text.isdigit():
            raise payments.PaymentSettingsError(f"День окна оплаты – число от 1 до {payments.WINDOW_LAST_DAY_MAX}")
        days.append(int(text) if text else None)
    error = payments.validate_window(*days)
    if error:
        raise payments.PaymentSettingsError(error)
    return Defaults(days[0], days[1], payments.parse_paid_month(month))


# ---------------------------------------------------------------------------
# Разбор файла
# ---------------------------------------------------------------------------

def _norm_words(text: str) -> list[str]:
    """Слова имени: нижний регистр, «ё» как «е», без знаков препинания."""
    text = (text or "").lower().replace("ё", "е")
    return [w for w in re.split(r"[^\w-]+", text) if w and not w.isdigit()]


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace("\xa0", " ").strip()


def _column_kind(header: str) -> str | None:
    h = header.lower().replace("ё", "е").replace(".", "").strip()
    if not h:
        return None
    if ("фамилия" in h and "имя" in h) or h in ("фио", "ученик", "ученица", "фи"):
        return "full"
    if "фамилия" in h:
        return "last"
    if h.startswith("имя"):
        return "first"
    if "тариф" in h:
        return "tariff"
    if any(word in h for word in ("сумм", "цен", "стоим", "к оплате")):
        return "amount"
    # После суммы: «Сумма в месяц» — это сумма, а не месяц.
    if "окно" in h:
        return "window"
    if "месяц" in h:
        return "month"
    if h.startswith("ник") or any(word in h for word in ("telegram", "телеграм", "username", "юзернейм")) \
            or h in ("тг", "tg"):
        return "nick"
    return None


def _find_header(rows: list[tuple]) -> tuple[int, dict[str, int]]:
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        columns: dict[str, int] = {}
        for col, value in enumerate(row):
            kind = _column_kind(_cell_text(value))
            if kind and kind not in columns:
                columns[kind] = col
        has_name = "full" in columns or ("first" in columns and "last" in columns)
        if (has_name or "nick" in columns) and "amount" in columns:
            return index, columns
    raise ImportFileError(
        "Не нашлись колонки. Нужны «Имя» и «Фамилия» (или одна «ФИО», или «Ник»), «Тариф» "
        "и «Сумма» – названия в первой строке таблицы."
    )


# Месяц словом: по началу слова, чтобы «ноябрь», «ноября» и «нояб.» читались одинаково.
_MONTH_STEMS = (
    ("янв", 1), ("фев", 2), ("мар", 3), ("апр", 4), ("май", 5), ("мая", 5),
    ("июн", 6), ("июл", 7), ("авг", 8), ("сен", 9), ("окт", 10), ("ноя", 11), ("дек", 12),
)


def parse_window(value) -> tuple[int, int] | None:
    """«10–15», «10-15», «с 10 по 15» → (10, 15). Пусто — None, мусор — ValueError.

    Excel превращает набранное «10-15» в дату 15 октября: из такой ячейки
    берём день и месяц как два дня окна."""
    if isinstance(value, (datetime, date)):
        days = sorted((value.day, value.month))
    else:
        text = _cell_text(value)
        if not text:
            return None
        found = re.findall(r"\d{1,2}", text)
        if len(found) != 2:
            raise ValueError(f"Не разобрать окно «{text}» – нужно как «10–15»")
        days = [int(found[0]), int(found[1])]
    error = payments.validate_window(days[0], days[1])
    if error:
        raise ValueError(error)
    return days[0], days[1]


def parse_month(value, today: date) -> date | None:
    """Первый месяц оплаты: «ноябрь», «ноябрь 2026», «11.2026», «2026-11» или
    дата из Excel → 1-е число месяца. Пусто — None, мусор — ValueError.

    Без года — ближайший такой месяц, начиная с прошлого: в октябре «ноябрь»
    и «сентябрь» значат этот год, «август» — следующий."""
    if isinstance(value, datetime):
        return value.date().replace(day=1)
    if isinstance(value, date):
        return value.replace(day=1)
    text = _cell_text(value).lower().replace("ё", "е")
    if not text:
        return None
    month = year = None
    year_match = re.search(r"\b(20\d{2})\b", text)
    if year_match:
        year = int(year_match.group(1))
    for stem, number in _MONTH_STEMS:
        if re.search(rf"\b{stem}", text):
            month = number
            break
    if month is None:
        numbers = [int(n) for n in re.findall(r"\d{1,4}", text) if len(n) <= 2]
        # «01.11.2026» — месяц второе число, «11.2026» и «11» — единственное.
        if len(numbers) == 2:
            month = numbers[1]
        elif len(numbers) == 1:
            month = numbers[0]
    if month is None or not 1 <= month <= 12:
        raise ValueError(f"Не разобрать месяц «{_cell_text(value)}» – нужно как «ноябрь» или «11.2026»")
    if year is None:
        # Ближайший: от прошлого месяца на десять вперёд. В ноябре «октябрь» —
        # этот год (первая оплата за месяц, чьё окно уже прошло), «сентябрь» — следующий.
        start = payments.add_months(today.replace(day=1), -1)
        year = start.year if month >= start.month else start.year + 1
    return date(year, month, 1)


def parse_tariff(raw: str) -> str | None:
    """«Я с вами», «"Я С ВАМИ"» → «Я С ВАМИ»; не узнали — None."""
    text = re.sub(r"[«»\"'“”„]", "", raw or "").replace("ё", "е").replace("Ё", "Е")
    text = " ".join(text.split()).upper()
    if not text:
        return None
    for code in TARIFFS:
        if text == code.replace("Ё", "Е"):
            return code
    return None


def parse_amount(value) -> int | None:
    """Сумма из ячейки: число или «13 255 ₽» / «13255,50 руб.» → копейки."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value * 100)) if value > 0 else None
    text = _cell_text(value).lower()
    text = re.sub(r"(₽|руб\.?|р\.)", "", text).replace(" ", "")
    amount = payments.rub_str_to_kop(text)
    return amount if amount else None


def parse_workbook(data: bytes, today: date | None = None) -> list[SheetRow]:
    """Строки первого листа. Битый файл и файл без нужных колонок — `ImportFileError`.

    `today` — от какого месяца считать месяц без года (по Москве)."""
    import openpyxl

    if today is None:
        today = datetime.now(MSK_TZ).date()

    if not data:
        raise ImportFileError("Файл пустой")
    if len(data) > MAX_FILE_BYTES:
        raise ImportFileError("Файл больше 2 МБ – это не похоже на список учеников")
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ImportFileError("Не получилось открыть файл. Нужен Excel в формате .xlsx") from None
    try:
        sheet = workbook.worksheets[0]
        rows = [tuple(r) for r in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()

    header_index, columns = _find_header(rows)

    def cell(row: tuple, kind: str):
        col = columns.get(kind)
        return row[col] if col is not None and col < len(row) else None

    result: list[SheetRow] = []
    for offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        if not any(_cell_text(v) for v in row):
            continue
        if len(result) >= MAX_ROWS:
            raise ImportFileError(f"В файле больше {MAX_ROWS} строк – это не похоже на список учеников")
        if "full" in columns:
            name_text = _cell_text(cell(row, "full"))
        else:
            name_text = f"{_cell_text(cell(row, 'first'))} {_cell_text(cell(row, 'last'))}".strip()
        item = SheetRow(line=offset, name_text=name_text, tokens=frozenset(_norm_words(name_text)))
        item.tariff_text = _cell_text(cell(row, "tariff"))
        if item.tariff_text:
            item.tariff = parse_tariff(item.tariff_text)
        raw_amount = cell(row, "amount")
        item.amount_kop = parse_amount(raw_amount)
        item.nick = normalize_tg_username(_cell_text(cell(row, "nick"))).lower()
        cell_error = None
        try:
            window = parse_window(cell(row, "window"))
            if window:
                item.window_start, item.window_end = window
            item.first_month = parse_month(cell(row, "month"), today)
        except ValueError as exc:
            cell_error = str(exc)

        if len(item.tokens) < 2 and not item.nick:
            item.error = "Нужны имя и фамилия или ник"
        elif cell_error:
            item.error = cell_error
        elif item.tariff_text and item.tariff is None:
            item.error = f"Не знаю тариф «{item.tariff_text}»"
        elif item.amount_kop is None:
            shown = _cell_text(raw_amount)
            item.error = f"Не разобрать сумму «{shown}»" if shown else "Нет суммы"
        result.append(item)
    if not result:
        raise ImportFileError("Под шапкой таблицы нет ни одной строки")
    return result


# ---------------------------------------------------------------------------
# Поиск учеников
# ---------------------------------------------------------------------------

def _student_keys(student: User) -> list[frozenset[str]]:
    """По каким наборам слов узнаём ученика: имя + фамилия, иначе «name»."""
    keys = []
    first, last = _norm_words(student.first_name or ""), _norm_words(student.last_name or "")
    if first and last:
        keys.append(frozenset(first + last))
    from_name = frozenset(_norm_words(student.name or ""))
    if len(from_name) >= 2:
        keys.append(from_name)
    return keys


def load_students(db: DBSession) -> list[User]:
    """Все ученики, включая архив и служебных — чтобы назвать причину пропуска."""
    return (
        db.query(User)
        .join(Role, Role.id == User.role_id)
        .filter(Role.rank == 1, User.deleted_at.is_(None))
        .order_by(User.last_name, User.first_name, User.id)
        .all()
    )


def is_writable(student: User) -> bool:
    return student.archived_at is None and student.id not in REPORT_EXCLUDED_USER_IDS


def _skip_reason(student: User) -> str:
    if student.id in REPORT_EXCLUDED_USER_IDS:
        return "Служебный аккаунт – вне оплат"
    return "Ученик в архиве – оплату не меняем"


def _match(row: SheetRow, index: list[tuple[frozenset[str], User]]) -> list[User]:
    """Совпадение — когда одно имя целиком входит в другое.

    В файле бывает ФИО с отчеством, а на платформе только имя и фамилия, и
    наоборот: у части учеников заполнено одно поле `name` из трёх слов. Два
    слова из файла находят такого ученика; если под них подошли двое —
    строка уйдёт в «однофамильцы», и ученика выберет человек.
    """
    found: dict[int, User] = {}
    for key, student in index:
        if key <= row.tokens or (len(row.tokens) >= 2 and row.tokens <= key):
            found.setdefault(student.id, student)
    return list(found.values())


# ---------------------------------------------------------------------------
# Что записать
# ---------------------------------------------------------------------------

def display_name(student: User) -> str:
    return f"{student.last_name or ''} {student.first_name or student.name}".strip()


def plan_settings(db: DBSession, student: User, amount_kop: int) -> tuple[str, int | None]:
    """Набор и своя цена, при которых ученик будет платить `amount_kop`."""
    current = student.pay_cohort or ""
    matching = [c for c in COHORTS if payments.reference_price_kop(db, student.tariff, c) == amount_kop]
    if current in matching:
        return current, None
    if len(matching) == 1:
        return matching[0], None
    # Ни один набор не даёт эту сумму (или дают оба, а набор не стоит) —
    # набор не трогаем, сумма становится своей ценой.
    return current, amount_kop


def row_defaults(row: SheetRow, defaults: Defaults) -> Defaults:
    """Окно и месяц из строки главнее общих с формы."""
    if row.window_start:
        start, end = row.window_start, row.window_end
    else:
        start, end = defaults.window_start, defaults.window_end
    return Defaults(start, end, row.paid_through or defaults.paid_through)


def is_holiday(student: User) -> bool:
    return student.program == PROGRAM_HOLIDAY


def plan_target(db: DBSession, student: User, row: SheetRow, defaults: Defaults) -> Target:
    """Всё, что строка запишет: сумма из файла плюс окно и месяц (из строки,
    иначе общие) в пустые поля."""
    own = row_defaults(row, defaults)
    cohort, price_kop = plan_settings(db, student, row.amount_kop)
    notes = []
    if payments.has_window(student):
        start, end = student.pay_window_start, student.pay_window_end
        if row.window_start and (row.window_start, row.window_end) != (start, end):
            notes.append(f"Окно у ученика уже стоит: {_window_text(start, end)}, загрузка его не меняет")
    else:
        start, end = own.window_start, own.window_end
    paid = payments.paid_month(student)
    if paid is None and own.paid_through:
        if start and end:
            paid = own.paid_through
        else:
            notes.append("Окна оплаты нет – оплаченный месяц не поставлен")
    next_tariff = None
    if is_holiday(student):
        notes.append(HOLIDAY_NOTE)
        if row.tariff:
            next_tariff = row.tariff
        else:
            notes.append("Тарифа в файле нет – ученик выберет его в анкете после перехода")
    return Target(start, end, cohort, price_kop, paid, ". ".join(notes), next_tariff)


HOLIDAY_NOTE = "Каникулярный набор: после оплаты перейдёт в годовой курс"


def _window_text(start: int | None, end: int | None) -> str:
    return f"{start}–{end}" if start and end else "–"


def _change_texts(student: User, target: Target) -> list[str]:
    changes = []
    old_window = (student.pay_window_start, student.pay_window_end)
    if old_window != (target.window_start, target.window_end):
        changes.append(
            f"окно: {_window_text(*old_window)} → {_window_text(target.window_start, target.window_end)}"
        )
    old_cohort = student.pay_cohort or ""
    if old_cohort != target.cohort:
        changes.append(
            f"набор: {COHORT_LABELS.get(old_cohort, '–')} → {COHORT_LABELS.get(target.cohort, '–')}"
        )
    if student.pay_price_kop != target.price_kop:
        old = payments.rub_text(student.pay_price_kop) if student.pay_price_kop else "по справочнику"
        new = payments.rub_text(target.price_kop) if target.price_kop else "по справочнику"
        changes.append(f"своя цена: {old} → {new}")
    old_month = payments.paid_month(student)
    if target.paid_through and old_month != target.paid_through:
        old = payments.period_label(old_month) if old_month else "–"
        changes.append(f"оплачено по: {old} → {payments.period_label(target.paid_through)}")
    if target.next_tariff is not None and target.next_tariff != (student.pay_next_tariff or ""):
        changes.append(
            f"тариф после оплаты: {_tariff_text(student.pay_next_tariff)} → {_tariff_text(target.next_tariff)}"
        )
    return changes


def _tariff_text(code: str | None) -> str:
    return TARIFF_DISPLAY.get(code or "", code or "–")


def _fill(db: DBSession, result: RowResult, student: User, defaults: Defaults) -> None:
    """Статус и изменения для строки с известным учеником."""
    row = result.row
    result.student = student
    result.target = plan_target(db, student, row, defaults)
    result.changes = _change_texts(student, result.target)
    result.note = ". ".join(filter(None, [result.note, result.target.note]))
    if is_holiday(student) and row.tariff and row.tariff not in TARIFFS_CURRENT:
        # «Тариф после оплаты» принимает только действующий — запись отказала бы.
        result.status = ROW_ERROR
        result.note = f"Тариф «{_tariff_text(row.tariff)}» больше не продаётся"
    elif is_holiday(student):
        # Своего тарифа у каникулярного нет: тариф из файла — тариф после оплаты.
        result.status = ROW_READY if result.changes else ROW_SAME
    elif row.tariff and row.tariff != student.tariff:
        result.status = ROW_TARIFF
        tariff_note = (
            f"В файле тариф «{_tariff_text(row.tariff)}», на платформе – "
            f"«{_tariff_text(student.tariff)}». Тариф загрузка не меняет"
        )
        result.note = ". ".join(filter(None, [tariff_note, result.note]))
    elif result.changes:
        result.status = ROW_READY
    else:
        result.status = ROW_SAME


def build_preview(db: DBSession, rows: list[SheetRow], defaults: Defaults = Defaults()) -> list[RowResult]:
    """Что произойдёт с каждой строкой. Ничего не пишет."""
    students = load_students(db)
    index = [(key, s) for s in students for key in _student_keys(s)]
    by_nick: dict[str, list[User]] = {}
    for s in students:
        nick = normalize_tg_username(s.tg_username or "").lower()
        if nick:
            by_nick.setdefault(nick, []).append(s)
    taken: set[int] = set()
    results: list[RowResult] = []
    for row in rows:
        result = RowResult(row=row, status=ROW_ERROR)
        results.append(result)
        if row.error:
            result.note = row.error
            continue
        # Ник точнее имени: у каникулярных он обязателен, однофамильцев у него
        # нет. Не нашёлся — ищем по имени (опечатка в нике), так и пишем.
        fallback = ""
        matches = by_nick.get(row.nick, []) if row.nick else []
        if row.nick and not matches and len(row.tokens) >= 2:
            matches = _match(row, index)
            if matches:
                fallback = f"По нику @{row.nick} не нашёлся – найден по имени"
        elif not row.nick:
            matches = _match(row, index)
        writable = [s for s in matches if is_writable(s)]
        if not matches:
            result.status = ROW_NOT_FOUND
            result.note = (
                f"Ученик @{row.nick} не найден – возможно, ещё не заходил на платформу"
                if row.nick else "Ученик с таким именем не найден – выберите вручную"
            )
        elif len(writable) > 1:
            result.status = ROW_AMBIGUOUS
            result.candidates = writable
            result.note = f"Учеников с таким именем: {len(writable)} – выберите нужного"
        elif not writable:
            result.status = ROW_SKIPPED
            result.student = matches[0]
            result.note = _skip_reason(matches[0])
        elif writable[0].id in taken:
            result.status = ROW_DUPLICATE
            result.student = writable[0]
            result.note = "Этот ученик уже есть выше в файле"
        else:
            taken.add(writable[0].id)
            _fill(db, result, writable[0], defaults)
        if fallback:
            result.note = ". ".join(filter(None, [fallback, result.note]))
    return results


# ---------------------------------------------------------------------------
# Запись
# ---------------------------------------------------------------------------

@dataclass
class ApplySummary:
    written: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)
    changed_ids: list[int] = field(default_factory=list)


def apply_import(
    db: DBSession,
    performed_by_id: int,
    rows: list[SheetRow],
    choices: dict[int, int],
    defaults: Defaults = Defaults(),
) -> ApplySummary:
    """Записать строки из `choices` («номер строки → id ученика»). Не коммитит.

    Ученика в выборе даёт человек: найденного по имени он подтвердил галочкой,
    ненайденного выбрал из списка. Поэтому здесь проверяется только, что это
    живой ученик вне архива и служебных, и что одного ученика не пишут дважды.
    """
    by_line = {row.line: row for row in rows}
    students = {s.id: s for s in load_students(db)}
    summary = ApplySummary()
    used: set[int] = set()
    for line, student_id in sorted(choices.items()):
        row = by_line.get(line)
        student = students.get(student_id)
        if row is None:
            summary.skipped.append(f"Строка {line}: её нет в файле")
            continue
        if row.error:
            summary.skipped.append(f"Строка {line}: {row.error}")
            continue
        if student is None or not is_writable(student):
            summary.skipped.append(f"Строка {line}: ученик не найден или вне оплат")
            continue
        if student.id in used:
            summary.skipped.append(f"Строка {line}: {display_name(student)} уже записан выше")
            continue
        used.add(student.id)
        target = plan_target(db, student, row, defaults)
        try:
            changed = payments.apply_payment_settings(
                db, performed_by_id, student,
                window_start=target.window_start,
                window_end=target.window_end,
                cohort=target.cohort,
                price_kop=target.price_kop,
                paid_through=target.paid_through,
                next_tariff=target.next_tariff,
            )
        except payments.PaymentSettingsError as exc:
            summary.skipped.append(f"Строка {line}: {exc}")
            continue
        if changed:
            summary.written += 1
            summary.changed_ids.append(student.id)
        else:
            summary.unchanged += 1
    return summary


def preview_json(results: list[RowResult]) -> list[dict]:
    """Строки предпросмотра для страницы."""
    out = []
    for r in results:
        item = {
            "line": r.row.line,
            "name": r.row.name_text or (f"@{r.row.nick}" if r.row.nick else ""),
            "nick": r.row.nick,
            "window": _window_text(r.row.window_start, r.row.window_end) if r.row.window_start else "",
            "first_month": payments.period_label(r.row.first_month) if r.row.first_month else "",
            "tariff": r.row.tariff_text,
            "amount": payments.rub_text(r.row.amount_kop) if r.row.amount_kop else "",
            "status": r.status,
            "note": r.note,
            "changes": r.changes,
            "student": None,
            "candidates": [{"id": s.id, "name": display_name(s)} for s in r.candidates],
        }
        if r.student is not None:
            item["student"] = {
                "id": r.student.id,
                "name": display_name(r.student),
                "tariff": _tariff_text(r.student.tariff),
            }
        out.append(item)
    return out


def counts(results: list[RowResult]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in results:
        out[r.status] = out.get(r.status, 0) + 1
    return out
