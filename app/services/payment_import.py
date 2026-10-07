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
оплаченный месяц файл не несёт — у ученика они остаются как были.

Что делаем с суммой. Совпала с ценой справочника для тарифа ученика в одном
из наборов — ставим этот набор и снимаем свою цену: подорожание набора тогда
дойдёт до ученика правкой справочника. Не совпала ни с одним — записываем
своей ценой. Тариф загрузка не меняет: смена тарифа — `apply_tariff_change`
со своими правилами, а файл заказчика для неё не основание.
"""
import io
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session as DBSession

from app.constants import REPORT_EXCLUDED_USER_IDS, TARIFF_DISPLAY, TARIFFS
from app.models.payment import COHORT_LABELS, COHORTS
from app.models.role import Role
from app.models.user import User
from app.services import payments

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


@dataclass
class RowResult:
    row: SheetRow
    status: str
    student: User | None = None
    candidates: list[User] = field(default_factory=list)
    note: str = ""
    cohort: str = ""
    price_kop: int | None = None
    changes: list[str] = field(default_factory=list)


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
    return None


def _find_header(rows: list[tuple]) -> tuple[int, dict[str, int]]:
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        columns: dict[str, int] = {}
        for col, value in enumerate(row):
            kind = _column_kind(_cell_text(value))
            if kind and kind not in columns:
                columns[kind] = col
        has_name = "full" in columns or ("first" in columns and "last" in columns)
        if has_name and "amount" in columns:
            return index, columns
    raise ImportFileError(
        "Не нашлись колонки. Нужны «Имя» и «Фамилия» (или одна «ФИО»), «Тариф» и «Сумма» – "
        "названия в первой строке таблицы."
    )


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


def parse_workbook(data: bytes) -> list[SheetRow]:
    """Строки первого листа. Битый файл и файл без нужных колонок — `ImportFileError`."""
    import openpyxl

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

        if len(item.tokens) < 2:
            item.error = "Нужны имя и фамилия"
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


def _change_texts(student: User, cohort: str, price_kop: int | None) -> list[str]:
    changes = []
    old_cohort = student.pay_cohort or ""
    if old_cohort != cohort:
        changes.append(
            f"набор: {COHORT_LABELS.get(old_cohort, '–')} → {COHORT_LABELS.get(cohort, '–')}"
        )
    if student.pay_price_kop != price_kop:
        old = payments.rub_text(student.pay_price_kop) if student.pay_price_kop else "по справочнику"
        new = payments.rub_text(price_kop) if price_kop else "по справочнику"
        changes.append(f"своя цена: {old} → {new}")
    return changes


def _tariff_text(code: str | None) -> str:
    return TARIFF_DISPLAY.get(code or "", code or "–")


def _fill(db: DBSession, result: RowResult, student: User) -> None:
    """Статус и изменения для строки с известным учеником."""
    row = result.row
    result.student = student
    result.cohort, result.price_kop = plan_settings(db, student, row.amount_kop)
    result.changes = _change_texts(student, result.cohort, result.price_kop)
    if row.tariff and row.tariff != student.tariff:
        result.status = ROW_TARIFF
        result.note = (
            f"В файле тариф «{_tariff_text(row.tariff)}», на платформе – "
            f"«{_tariff_text(student.tariff)}». Тариф загрузка не меняет"
        )
    elif result.changes:
        result.status = ROW_READY
    else:
        result.status = ROW_SAME


def build_preview(db: DBSession, rows: list[SheetRow]) -> list[RowResult]:
    """Что произойдёт с каждой строкой. Ничего не пишет."""
    students = load_students(db)
    index = [(key, s) for s in students for key in _student_keys(s)]
    taken: set[int] = set()
    results: list[RowResult] = []
    for row in rows:
        result = RowResult(row=row, status=ROW_ERROR)
        results.append(result)
        if row.error:
            result.note = row.error
            continue
        matches = _match(row, index)
        writable = [s for s in matches if is_writable(s)]
        if not matches:
            result.status = ROW_NOT_FOUND
            result.note = "Ученик с таким именем не найден – выберите вручную"
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
            _fill(db, result, writable[0])
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
        cohort, price_kop = plan_settings(db, student, row.amount_kop)
        try:
            changed = payments.apply_payment_settings(
                db, performed_by_id, student,
                window_start=student.pay_window_start,
                window_end=student.pay_window_end,
                cohort=cohort,
                price_kop=price_kop,
                paid_through=payments.paid_month(student),
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
            "name": r.row.name_text,
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
