"""Отчёт месяца по тарифам: кто пришёл, ушёл, перешёл (Александрия 22.10.2026).

Тариф в прошлом восстанавливается по журналу `tariff_change`; считаются
только действующие тарифы, отработавший «УВЕРЕННЫЙ» — «без тарифа».
"""
from datetime import datetime, timezone

from app.constants import PROGRAM_HOLIDAY, TARIFF_CONFIDENT_MAX, TARIFF_SELF, TARIFF_WITH_YOU
from app.models.audit_log import AuditLog
from app.services.activity_stats import get_month_tariff_flow

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def _at(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 12, tzinfo=timezone.utc)


def _student(user_factory, db, vk_id, name, tariff, created):
    student = user_factory(vk_id=vk_id, name=name, tariff=tariff)
    student.created_at = created
    db.commit()
    return student


def _log(db, student, action, details, at):
    db.add(AuditLog(action=action, performed_by_id=student.id, target_user_id=student.id,
                    details=details, created_at=at))
    db.commit()


def _month(flow, key):
    return next(month for month in flow["months"] if month["key"] == key)


def _by_label(month):
    return {row["label"]: row for row in month["tariffs"]}


def test_came_left_switched_by_month(db, user_factory):
    self_july = _student(user_factory, db, 840_001, "Сам Июль", TARIFF_SELF, _at(7, 15))
    # Пришёл без тарифа, тариф получил в сентябре.
    trial = _student(user_factory, db, 840_002, "Пробник", TARIFF_WITH_YOU, _at(8, 10))
    _log(db, trial, "tariff_change", "tariff: — → Я С ВАМИ (срок доступа снят)", _at(9, 3))
    # Пришёл на «Уверенный максимум», в сентябре перешёл на «Я сам».
    switcher = _student(user_factory, db, 840_003, "Переход", TARIFF_SELF, _at(7, 20))
    _log(db, switcher, "tariff_change", f"tariff: {TARIFF_CONFIDENT_MAX} → {TARIFF_SELF}", _at(9, 12))
    # Отработавший дефолт «УВЕРЕННЫЙ» — ещё не пришёл; пришёл, выбрав тариф.
    legacy = _student(user_factory, db, 840_004, "Старый дефолт", TARIFF_CONFIDENT_MAX, _at(7, 25))
    _log(db, legacy, "tariff_change", f"tariff: УВЕРЕННЫЙ → {TARIFF_CONFIDENT_MAX}", _at(8, 2))
    # Ушёл в октябре: архив.
    archived = _student(user_factory, db, 840_005, "Архив", TARIFF_SELF, _at(7, 16))
    archived.archived_at, archived.is_active = _at(10, 5), False
    # Ушёл в октябре: блокировка держится.
    blocked = _student(user_factory, db, 840_006, "Блок", TARIFF_WITH_YOU, _at(7, 17))
    blocked.is_active = False
    db.commit()
    _log(db, blocked, "user_block", "", _at(10, 8))
    # Каникулярный в отчёт не идёт.
    holiday = _student(user_factory, db, 840_007, "Каникулы", TARIFF_SELF, _at(10, 1))
    holiday.program = PROGRAM_HOLIDAY
    db.commit()

    flow = get_month_tariff_flow(db, now=NOW)

    assert [month["key"] for month in flow["months"]] == ["2026-10", "2026-09", "2026-08", "2026-07"]

    july = _month(flow, "2026-07")
    assert july["came"] == 4  # сам, переход, архив, блок
    assert _by_label(july)["Уверенный максимум"]["came"] == 1

    august = _month(flow, "2026-08")
    assert [row["name"] for row in august["came_rows"]] == ["Старый дефолт"]
    assert august["start"] == 4

    september = _month(flow, "2026-09")
    assert [row["name"] for row in september["came_rows"]] == ["Пробник"]
    assert september["switched"] == 1
    assert september["pairs"] == [{"from": "Уверенный максимум", "to": "Я сам", "count": 1}]

    october = _month(flow, "2026-10")
    assert october["came"] == 0
    assert sorted(row["name"] for row in october["left_rows"]) == ["Архив", "Блок"]
    assert october["start"] == 6
    assert october["end"] == 4
    tariffs = _by_label(october)
    assert tariffs["Я сам"]["left"] == 1 and tariffs["Я сам"]["start"] == 3
    assert tariffs["Я сам"]["left_pct"] == 33
    assert october["left_rows"][0]["nick"]


def test_unblocked_student_has_not_left(db, user_factory):
    student = _student(user_factory, db, 840_010, "Вернулся", TARIFF_SELF, _at(7, 15))
    _log(db, student, "user_block", "", _at(9, 1))
    _log(db, student, "user_unblock", "", _at(9, 2))

    flow = get_month_tariff_flow(db, now=NOW)

    assert _month(flow, "2026-09")["left"] == 0
    assert _month(flow, "2026-10")["end"] == 1


def test_student_without_current_tariff_is_not_in_report(db, user_factory):
    """Пробник без тарифа и отработавший тариф — не «пришёл» и не «ушёл»."""
    trial = _student(user_factory, db, 840_020, "Без тарифа", "", _at(9, 1))
    trial.archived_at, trial.is_active = _at(10, 2), False
    _student(user_factory, db, 840_021, "Старый", "УВЕРЕННЫЙ", _at(9, 1))
    db.commit()

    flow = get_month_tariff_flow(db, now=NOW)

    assert all(month["came"] == 0 and month["left"] == 0 for month in flow["months"])


def test_activity_page_shows_report_only_for_annual_course(admin_client, db, user_factory):
    client, _ = admin_client
    _student(user_factory, db, 840_030, "Сам", TARIFF_SELF, datetime.now(timezone.utc))

    annual = client.get("/cabinet/superadmin/activity").text
    holiday = client.get("/cabinet/superadmin/activity?program=holiday").text

    assert "Отчёт месяца по тарифам" in annual and "data-tf-month" in annual
    assert "Отчёт месяца по тарифам" not in holiday
