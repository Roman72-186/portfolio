"""Поиск ученика: по имени, нику и номеру на платформе — одно правило на все
списки.

Номер (владелец 14.10.2026: «добавить везде поиск по id»). Запрос из одних
цифр — «256», «#256», «№ 256», «id 256» — ищет ученика с этим номером и только
его. Точное совпадение ничего не теряет: в имени цифр не бывает, а ник в
Telegram с цифры начинаться не может. Подстрокой номер не ищем — «25» нашёл
бы и #125, и #250.

JS-двойники: номер — `static/js/student-search.js` (`studentIdFromQuery`),
нормализация — `normalizeStudentSearch` в `static/js/cabinet_students.js`.
"""
import re
import unicodedata

_ID_QUERY = re.compile(r"^\s*(?:[#№]\s*|id\s*)?(\d{1,9})\s*$", re.IGNORECASE)


def student_id_query(q: str | None) -> int | None:
    """Номер ученика из поискового запроса; `None` — запрос не номер."""
    match = _ID_QUERY.match(q or "")
    return int(match.group(1)) if match else None


def normalize_student_search(value: object) -> str:
    """NFKC, регистр, ё→е, схлопнутые пробелы — как `normalizeStudentSearch`
    на «Учениках», чтобы один запрос находил одних и тех же учеников."""
    text = unicodedata.normalize("NFKC", str(value or "")).lower().replace("ё", "е")
    return " ".join(text.split())


def student_matches(student, q: str | None, *, search_contacts: bool = True) -> bool:
    """Подходит ли ученик под запрос. Пустой запрос подходит всем.

    Номер — точное совпадение. Иначе каждое слово запроса должно найтись в
    фамилии, имени или (при `search_contacts`) нике Telegram; ведущий «@»
    у слова не мешает.
    """
    q_id = student_id_query(q)
    if q_id is not None:
        return student.id == q_id
    tokens = [t.lstrip("@") for t in normalize_student_search(q).split(" ") if t.lstrip("@")]
    if not tokens:
        return True
    parts = [student.last_name, student.first_name, student.name]
    if search_contacts:
        # vk_id — внутренний номер, людям не показывается (VK удалён
        # 29.09.2026), искать по нему некому.
        parts.append((student.tg_username or "").lstrip("@"))
    haystack = normalize_student_search(" ".join(str(p) for p in parts if p))
    return all(token in haystack for token in tokens)
