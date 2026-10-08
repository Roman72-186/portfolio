"""«Оплата списком»: загрузка Excel заказчика (`services/payment_import.py`).

Проверяется: разбор файла в разных записях, поиск ученика по имени и
фамилии, все статусы строк, выбор набора или своей цены по сумме, запись
только отмеченных строк через `apply_payment_settings` и права.
"""
import io
import json
from datetime import date

import openpyxl
import pytest

from app.models.audit_log import AuditLog
from app.models.payment import COHORT_BEFORE, COHORT_FROM, PaymentPrice
from app.services import payment_import as pi
from app.services import payments as ps


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HEADER = ["Имя", "Фамилия", "Тариф", "Сумма"]


@pytest.fixture()
def prices(db):
    db.add_all([
        PaymentPrice(tariff="Я САМ", cohort=COHORT_BEFORE, amount_kop=677500),
        PaymentPrice(tariff="Я САМ", cohort=COHORT_FROM, amount_kop=677500),
        PaymentPrice(tariff="Я С ВАМИ", cohort=COHORT_BEFORE, amount_kop=1275500),
        PaymentPrice(tariff="Я С ВАМИ", cohort=COHORT_FROM, amount_kop=1325500),
    ])
    db.commit()


@pytest.fixture()
def make_student(db, user_factory):
    counter = iter(range(980_001, 980_999))

    def _make(first, last, tariff="Я С ВАМИ", **fields):
        student = user_factory(vk_id=next(counter), name=f"{first} {last}", tariff=tariff)
        student.first_name, student.last_name = first, last
        for key, value in fields.items():
            setattr(student, key, value)
        db.commit()
        return student
    return _make


def _preview(db, rows, defaults=pi.Defaults()):
    results = pi.build_preview(db, pi.parse_workbook(_xlsx(rows)), defaults)
    return {r.row.line: r for r in results}


# ── Разбор файла ─────────────────────────────────────────────────────────────

def test_parse_finds_header_below_title_in_any_column_order():
    rows = pi.parse_workbook(_xlsx([
        ["Оплата за октябрь"],
        [],
        ["Сумма, ₽", "Тариф", "Фамилия", "Имя"],
        [13255, "«Я с вами»", "Иванова", "Анна"],
        [None, None, None, None],
        ["12 000,50 ₽", "", "Петров", "Пётр"],
    ]))

    assert [r.line for r in rows] == [4, 6]
    anna, petr = rows
    assert anna.tokens == frozenset({"анна", "иванова"})
    assert (anna.tariff, anna.amount_kop, anna.error) == ("Я С ВАМИ", 1325500, None)
    # Пустой тариф — не ошибка, просто без сверки; «ё» читается как «е».
    assert (petr.tariff, petr.amount_kop, petr.tokens) == (None, 1200050, frozenset({"петр", "петров"}))


def test_parse_accepts_single_name_column():
    rows = pi.parse_workbook(_xlsx([["ФИО", "Тариф", "Сумма"], ["Иванова Анна Сергеевна", "Я сам", "6775"]]))

    assert rows[0].tokens == frozenset({"иванова", "анна", "сергеевна"})
    assert (rows[0].tariff, rows[0].amount_kop) == ("Я САМ", 677500)


@pytest.mark.parametrize("row, error", [
    (["Анна", "", "Я с вами", 13255], "Нужны имя и фамилия"),
    (["Анна", "Иванова", "Премиум", 13255], "Не знаю тариф «Премиум»"),
    (["Анна", "Иванова", "Я с вами", ""], "Нет суммы"),
    (["Анна", "Иванова", "Я с вами", "много"], "Не разобрать сумму «много»"),
    (["Анна", "Иванова", "Я с вами", 0], "Не разобрать сумму «0»"),
])
def test_parse_marks_bad_rows(row, error):
    assert pi.parse_workbook(_xlsx([HEADER, row]))[0].error == error


@pytest.mark.parametrize("data, message", [
    (b"", "Файл пустой"),
    (b"not an excel file", "Не получилось открыть файл"),
    (_xlsx([["Имя", "Город"], ["Анна", "Москва"]]), "Не нашлись колонки"),
    (_xlsx([HEADER]), "нет ни одной строки"),
])
def test_parse_refuses_whole_file(data, message):
    with pytest.raises(pi.ImportFileError, match=message):
        pi.parse_workbook(data)


# ── Предпросмотр ─────────────────────────────────────────────────────────────

def test_sum_equal_to_reference_sets_cohort(db, prices, make_student):
    anna = make_student("Анна", "Иванова")

    row = _preview(db, [HEADER, ["анна", "ИВАНОВА", "Я с вами", 13255]])[2]

    assert (row.status, row.student.id) == (pi.ROW_READY, anna.id)
    assert (row.target.cohort, row.target.price_kop) == (COHORT_FROM, None)
    assert row.changes == ["набор: – → с 01.09.2026"]


def test_sum_off_reference_becomes_own_price(db, prices, make_student):
    make_student("Анна", "Иванова", pay_cohort=COHORT_BEFORE)

    row = _preview(db, [HEADER, ["Анна", "Иванова", "Я с вами", 12000]])[2]

    assert (row.status, row.target.cohort, row.target.price_kop) == (pi.ROW_READY, COHORT_BEFORE, 1200000)
    assert row.changes == ["своя цена: по справочнику → 12 000 ₽"]


def test_same_price_in_both_cohorts(db, prices, make_student):
    with_cohort = make_student("Анна", "Иванова", tariff="Я САМ", pay_cohort=COHORT_BEFORE)
    without = make_student("Борис", "Смирнов", tariff="Я САМ")

    rows = _preview(db, [HEADER, ["Анна", "Иванова", "Я сам", 6775], ["Борис", "Смирнов", "Я сам", 6775]])

    # Набор стоит — он и остаётся, менять нечего.
    assert (rows[2].status, rows[2].student.id) == (pi.ROW_SAME, with_cohort.id)
    # Набора нет, а сумма подходит к обоим — набор не угадываем, цена своя.
    assert (rows[3].student.id, rows[3].target.cohort, rows[3].target.price_kop) == (without.id, "", 677500)


def test_order_of_name_and_yo_do_not_matter(db, prices, make_student):
    petr = make_student("Пётр", "Семёнов")

    row = _preview(db, [["ФИО", "Тариф", "Сумма"], ["Семенов Петр", "Я с вами", 13255]])[2]

    assert row.student.id == petr.id


def test_two_words_find_student_with_patronymic_in_name(db, prices, user_factory):
    """У части учеников заполнено только `name` из трёх слов, без имени и фамилии."""
    student = user_factory(vk_id=980_900, name="Иванова Анна Сергеевна", tariff="Я С ВАМИ")
    user_factory(vk_id=980_901, name="Иванова Мария Сергеевна", tariff="Я С ВАМИ")

    rows = _preview(db, [HEADER, ["Анна", "Иванова", "Я с вами", 13255], ["Сергеевна", "Иванова", "Я с вами", 13255]])

    assert (rows[2].status, rows[2].student.id) == (pi.ROW_READY, student.id)
    # Два слова подходят к двум ученикам — решает человек.
    assert rows[3].status == pi.ROW_AMBIGUOUS


def test_other_tariff_needs_confirmation(db, prices, make_student):
    make_student("Анна", "Иванова", tariff="Я САМ")

    row = _preview(db, [HEADER, ["Анна", "Иванова", "Уверенный максимум", 19255]])[2]

    assert row.status == pi.ROW_TARIFF
    assert "«Уверенный максимум»" in row.note and "«Я сам»" in row.note


def test_not_found_ambiguous_duplicate(db, prices, make_student):
    first = make_student("Анна", "Иванова")
    second = make_student("Анна", "Иванова")
    make_student("Борис", "Смирнов")

    rows = _preview(db, [
        HEADER,
        ["Вера", "Кузнецова", "Я с вами", 13255],
        ["Анна", "Иванова", "Я с вами", 13255],
        ["Борис", "Смирнов", "Я с вами", 13255],
        ["Смирнов", "Борис", "Я с вами", 13255],
    ])

    assert rows[2].status == pi.ROW_NOT_FOUND
    assert rows[3].status == pi.ROW_AMBIGUOUS
    assert {s.id for s in rows[3].candidates} == {first.id, second.id}
    assert rows[4].status == pi.ROW_READY
    assert rows[5].status == pi.ROW_DUPLICATE


def test_service_and_archived_skipped(db, prices, make_student, monkeypatch):
    from datetime import datetime, timezone

    service = make_student("Роман", "Махметов")
    make_student("Вера", "Кузнецова", archived_at=datetime.now(timezone.utc))
    monkeypatch.setattr(pi, "REPORT_EXCLUDED_USER_IDS", {service.id})

    rows = _preview(db, [HEADER, ["Роман", "Махметов", "Я с вами", 13255], ["Вера", "Кузнецова", "Я с вами", 13255]])

    assert (rows[2].status, rows[2].note) == (pi.ROW_SKIPPED, "Служебный аккаунт – вне оплат")
    assert (rows[3].status, rows[3].note) == (pi.ROW_SKIPPED, "Ученик в архиве – оплату не меняем")


def test_preview_writes_nothing(db, prices, make_student):
    anna = make_student("Анна", "Иванова")

    _preview(db, [HEADER, ["Анна", "Иванова", "Я с вами", 12000]])
    db.refresh(anna)

    assert (anna.pay_cohort, anna.pay_price_kop) in ((None, None), ("", None))
    assert db.query(AuditLog).filter(AuditLog.action == "payment_settings_change").count() == 0


# ── Общие окно и месяц ───────────────────────────────────────────────────────

OCTOBER = date(2026, 10, 1)


@pytest.mark.parametrize("start, end, month, expected", [
    ("", "", "", pi.Defaults()),
    ("10", "15", "2026-10", pi.Defaults(10, 15, OCTOBER)),
    ("", "", "2026-10", pi.Defaults(None, None, OCTOBER)),
])
def test_parse_defaults(start, end, month, expected):
    assert pi.parse_defaults(start, end, month) == expected


@pytest.mark.parametrize("start, end, month", [("10", "", ""), ("15", "10", ""), ("x", "15", ""), ("10", "15", "окт")])
def test_parse_defaults_refuses(start, end, month):
    with pytest.raises(ps.PaymentSettingsError):
        pi.parse_defaults(start, end, month)


def test_defaults_fill_only_empty_fields(db, prices, make_student, user_factory):
    chief = user_factory(vk_id=981_010, name="Главный", is_admin=True, role_name="админ")
    fresh = make_student("Анна", "Иванова")
    own = make_student("Борис", "Смирнов")
    ps.apply_payment_settings(db, chief.id, own, window_start=20, window_end=25,
                              cohort=COHORT_FROM, price_kop=None, paid_through=date(2026, 9, 1))
    db.commit()
    defaults = pi.Defaults(10, 15, OCTOBER)

    rows = _preview(db, [HEADER, ["Анна", "Иванова", "Я с вами", 13255], ["Борис", "Смирнов", "Я с вами", 13255]],
                    defaults)

    assert rows[2].changes == ["окно: – → 10–15", "набор: – → с 01.09.2026", "оплачено по: – → октябрь 2026"]
    # Своё окно и свой месяц из карточки загрузка не перетирает.
    assert rows[3].status == pi.ROW_SAME

    file_rows = pi.parse_workbook(_xlsx([HEADER, ["Анна", "Иванова", "Я с вами", 13255]]))
    pi.apply_import(db, chief.id, file_rows, {2: fresh.id}, defaults)
    db.commit()
    db.refresh(fresh)
    assert (fresh.pay_window_start, fresh.pay_window_end) == (10, 15)
    assert ps.paid_month(fresh) == OCTOBER
    assert fresh.paid_until == ps.paid_until_after(fresh, OCTOBER)


def test_month_without_window_is_not_set(db, prices, make_student):
    make_student("Анна", "Иванова")

    row = _preview(db, [HEADER, ["Анна", "Иванова", "Я с вами", 13255]], pi.Defaults(None, None, OCTOBER))[2]

    assert row.target.paid_through is None
    assert row.note == "Окна оплаты нет – оплаченный месяц не поставлен"


def test_routes_pass_defaults_and_refuse_bad_ones(db, client, session_factory, prices, make_student, staff):
    anna = make_student("Анна", "Иванова")
    client.cookies.set("session_id", session_factory(staff["chief"]).id)
    rows = [HEADER, ["Анна", "Иванова", "Я с вами", 13255]]
    form = {"window_start": "10", "window_end": "15", "paid_month": "2026-10"}

    bad = client.post("/cabinet/superadmin/payment-import/preview", files=_upload(rows),
                      data={**form, "window_end": "5"})
    applied = client.post("/cabinet/superadmin/payment-import/apply", files=_upload(rows),
                          data={**form, "choices": json.dumps([{"line": 2, "user_id": anna.id}])})

    assert bad.status_code == 400
    assert applied.status_code == 200, applied.text
    db.refresh(anna)
    assert (anna.pay_window_start, ps.paid_month(anna)) == (10, OCTOBER)


# ── Запись ───────────────────────────────────────────────────────────────────

def test_apply_keeps_window_and_paid_month(db, prices, make_student, user_factory):
    chief = user_factory(vk_id=981_001, name="Главный", is_admin=True, role_name="админ")
    anna = make_student("Анна", "Иванова", pay_window_start=10, pay_window_end=15)
    ps.apply_payment_settings(db, chief.id, anna, window_start=10, window_end=15,
                              cohort=COHORT_BEFORE, price_kop=None, paid_through=date(2026, 10, 1))
    db.commit()
    paid_until = anna.paid_until

    rows = pi.parse_workbook(_xlsx([HEADER, ["Анна", "Иванова", "Я с вами", 13255]]))
    summary = pi.apply_import(db, chief.id, rows, {2: anna.id})
    db.commit()
    db.refresh(anna)

    assert (summary.written, summary.changed_ids) == (1, [anna.id])
    assert (anna.pay_window_start, anna.pay_window_end, anna.pay_cohort) == (10, 15, COHORT_FROM)
    assert ps.paid_month(anna) == date(2026, 10, 1)
    assert anna.paid_until == paid_until


def test_apply_refuses_error_rows_service_and_repeats(db, prices, make_student, user_factory, monkeypatch):
    chief = user_factory(vk_id=981_002, name="Главный", is_admin=True, role_name="админ")
    anna = make_student("Анна", "Иванова")
    service = make_student("Роман", "Махметов")
    monkeypatch.setattr(pi, "REPORT_EXCLUDED_USER_IDS", {service.id})

    rows = pi.parse_workbook(_xlsx([
        HEADER,
        ["Анна", "Иванова", "Я с вами", 13255],
        ["Анна", "Иванова", "Я с вами", 12000],
        ["Роман", "Махметов", "Я с вами", 13255],
        ["Вера", "", "Я с вами", 13255],
    ]))
    summary = pi.apply_import(db, chief.id, rows, {2: anna.id, 3: anna.id, 4: service.id, 5: anna.id, 9: anna.id})

    assert summary.written == 1
    assert summary.skipped == [
        "Строка 3: Иванова Анна уже записан выше",
        "Строка 4: ученик не найден или вне оплат",
        "Строка 5: Нужны имя и фамилия",
        "Строка 9: её нет в файле",
    ]


# ── Роуты и права ────────────────────────────────────────────────────────────

@pytest.fixture()
def staff(user_factory):
    return {
        "chief": user_factory(vk_id=982_001, name="Главный", is_admin=True, role_name="суперадмин"),
        "curator": user_factory(vk_id=982_002, name="Куратор", role_name="куратор"),
    }


def _upload(rows):
    return {"file": ("list.xlsx", _xlsx(rows), "application/octet-stream")}


def test_routes_preview_then_apply_with_manual_pick(db, client, session_factory, prices, make_student, staff):
    anna = make_student("Анна", "Иванова")
    nastya = make_student("Настя", "Смирнова")
    client.cookies.set("session_id", session_factory(staff["chief"]).id)
    rows = [HEADER, ["Анна", "Иванова", "Я с вами", 12000], ["Анастасия", "Смирнова", "Я с вами", 13255]]

    page = client.get("/cabinet/superadmin/payment-import")
    assert page.status_code == 200 and "Оплата списком" in page.text

    preview = client.post("/cabinet/superadmin/payment-import/preview", files=_upload(rows))
    assert preview.status_code == 200, preview.text
    statuses = [r["status"] for r in preview.json()["rows"]]
    assert statuses == [pi.ROW_READY, pi.ROW_NOT_FOUND]

    choices = json.dumps([{"line": 2, "user_id": anna.id}, {"line": 3, "user_id": nastya.id}])
    applied = client.post("/cabinet/superadmin/payment-import/apply", files=_upload(rows), data={"choices": choices})

    assert applied.status_code == 200, applied.text
    assert applied.json()["written"] == 2
    db.refresh(anna)
    db.refresh(nastya)
    assert anna.pay_price_kop == 1200000
    assert (nastya.pay_cohort, nastya.pay_price_kop) == (COHORT_FROM, None)


def test_routes_refuse_bad_file_and_empty_choice(db, client, session_factory, staff):
    client.cookies.set("session_id", session_factory(staff["chief"]).id)

    bad = client.post("/cabinet/superadmin/payment-import/preview",
                      files={"file": ("list.xlsx", b"garbage", "application/octet-stream")})
    empty = client.post("/cabinet/superadmin/payment-import/apply",
                        files=_upload([HEADER, ["Анна", "Иванова", "Я с вами", 1]]), data={"choices": "[]"})

    assert bad.status_code == 400 and "Не получилось открыть файл" in bad.json()["detail"]
    assert empty.status_code == 400


def test_curator_cannot_use_import(db, client, session_factory, prices, make_student, staff):
    anna = make_student("Анна", "Иванова")
    client.cookies.set("session_id", session_factory(staff["curator"]).id)
    rows = [HEADER, ["Анна", "Иванова", "Я с вами", 12000]]

    page = client.get("/cabinet/superadmin/payment-import", follow_redirects=False)
    applied = client.post("/cabinet/superadmin/payment-import/apply", files=_upload(rows),
                          data={"choices": json.dumps([{"line": 2, "user_id": anna.id}])},
                          headers={"Accept": "application/json"})

    assert page.status_code in (302, 303, 403)
    assert applied.status_code == 403
    db.refresh(anna)
    assert anna.pay_price_kop is None
