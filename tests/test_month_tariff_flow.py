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


def test_activity_page_tiles_first_and_all_cards_closed(admin_client, db, user_factory):
    """Владелец 27.10.2026: «все вкладки должны быть закрыты… цифры должны быть
    сверху всегда». До этого первым шёл раскрытый отчёт по тарифам, а плитки —
    под ним."""
    import re

    client, _ = admin_client
    _student(user_factory, db, 840_040, "Сам", TARIFF_SELF, datetime.now(timezone.utc))

    html = client.get("/cabinet/superadmin/activity").text
    students = html[html.index('id="actPanelStudents"'):html.index('id="actPanelCurators"')]

    assert students.index("Заходили за 7 дней") < students.index("<details")
    assert "data-tariff-flow" in students
    assert not re.search(r"<details[^>]*\sopen[\s>]", html)


# ── Правки Александрии 26.10.2026 ──────────────────────────────────────────


def test_left_by_reached_access_deadline(db, user_factory):
    """Уходящим школа закрывает доступ сроком (`access_state.access_left_at`):
    без этого отчёт видел одного ушедшего из шести."""
    left = _student(user_factory, db, 840_050, "Срок", TARIFF_WITH_YOU, _at(7, 15))
    left.access_until = _at(10, 9)
    later = _student(user_factory, db, 840_051, "Срок впереди", TARIFF_SELF, _at(7, 15))
    later.access_until = datetime(2026, 11, 5, tzinfo=timezone.utc)
    db.commit()

    october = _month(get_month_tariff_flow(db, now=NOW), "2026-10")

    assert [(row["name"], row["at"]) for row in october["left_rows"]] == [("Срок", "09.10.2026")]
    assert october["end"] == 1


def test_back_and_forth_in_month_is_not_a_switch(db, user_factory):
    student = _student(user_factory, db, 840_052, "Туда-обратно", TARIFF_WITH_YOU, _at(7, 15))
    _log(db, student, "tariff_change", f"tariff: {TARIFF_WITH_YOU} → {TARIFF_SELF}", _at(10, 13))
    _log(db, student, "tariff_change", f"tariff: {TARIFF_SELF} → {TARIFF_WITH_YOU}",
         datetime(2026, 10, 13, 13, tzinfo=timezone.utc))

    october = _month(get_month_tariff_flow(db, now=NOW), "2026-10")

    assert october["switched"] == 0 and october["pairs"] == []


def test_several_switches_in_month_collapse_to_one(db, user_factory):
    student = _student(user_factory, db, 840_053, "Два шага", TARIFF_SELF, _at(7, 15))
    _log(db, student, "tariff_change", f"tariff: {TARIFF_SELF} → {TARIFF_WITH_YOU}", _at(10, 3))
    _log(db, student, "tariff_change", f"tariff: {TARIFF_WITH_YOU} → {TARIFF_CONFIDENT_MAX}", _at(10, 10))

    october = _month(get_month_tariff_flow(db, now=NOW), "2026-10")

    assert october["pairs"] == [{"from": "Я сам", "to": "Уверенный максимум", "count": 1}]
    assert october["switch_rows"][0]["at"] == "10.10.2026"


def test_switched_and_left_in_same_month_counts_only_as_left(db, user_factory):
    """Смирнова: перевели на «Я сам», а она ушла — только в «Ушли»."""
    student = _student(user_factory, db, 840_054, "Ушла", TARIFF_SELF, _at(7, 15))
    _log(db, student, "tariff_change", f"tariff: {TARIFF_WITH_YOU} → {TARIFF_SELF}", _at(10, 5))
    student.access_until = _at(10, 15)
    db.commit()

    october = _month(get_month_tariff_flow(db, now=NOW), "2026-10")

    assert october["switched"] == 0
    assert [(row["name"], row["tariff"]) for row in october["left_rows"]] == [("Ушла", "Я сам")]


def test_correction_is_not_a_switch_and_fixes_arrival_tariff(db, user_factory):
    """Ученик по ошибке выбрал «Я с вами», школа поправила на «Уверенный
    максимум» с пометкой «исправление»: пришёл сразу на УМ, перехода нет."""
    student = _student(user_factory, db, 840_055, "Исправили", TARIFF_CONFIDENT_MAX, _at(9, 20))
    _log(db, student, "tariff_change",
         f"tariff: {TARIFF_WITH_YOU} → {TARIFF_CONFIDENT_MAX} (исправление)", _at(10, 13))

    flow = get_month_tariff_flow(db, now=NOW)
    september, october = _month(flow, "2026-09"), _month(flow, "2026-10")

    assert [(row["name"], row["tariff"]) for row in september["came_rows"]] == [("Исправили", "Уверенный максимум")]
    assert _by_label(october)["Уверенный максимум"]["start"] == 1
    assert october["switched"] == 0


def test_correction_after_real_switch_rewrites_only_that_switch(db, user_factory):
    student = _student(user_factory, db, 840_056, "Перешёл и поправили", TARIFF_CONFIDENT_MAX, _at(7, 15))
    _log(db, student, "tariff_change", f"tariff: {TARIFF_SELF} → {TARIFF_WITH_YOU}", _at(9, 10))
    _log(db, student, "tariff_change",
         f"tariff: {TARIFF_WITH_YOU} → {TARIFF_CONFIDENT_MAX} (срок доступа снят) (исправление)", _at(10, 2))

    flow = get_month_tariff_flow(db, now=NOW)

    assert _month(flow, "2026-09")["pairs"] == [{"from": "Я сам", "to": "Уверенный максимум", "count": 1}]
    assert _month(flow, "2026-10")["switched"] == 0


def test_tariff_route_writes_correction_mark(admin_client, db, user_factory):
    client, _ = admin_client
    student = user_factory(vk_id=840_057, name="Галочка", tariff=TARIFF_WITH_YOU)

    resp = client.post(f"/cabinet/superadmin/users/{student.id}/tariff",
                       data={"tariff": TARIFF_CONFIDENT_MAX, "correction": "1"})
    plain = client.post(f"/cabinet/superadmin/users/{student.id}/tariff",
                        data={"tariff": TARIFF_SELF})

    assert resp.status_code == 200 and plain.status_code == 200
    details = [row.details for row in db.query(AuditLog)
               .filter(AuditLog.target_user_id == student.id, AuditLog.action == "tariff_change")
               .order_by(AuditLog.id)]
    assert details == [
        f"tariff: {TARIFF_WITH_YOU} → {TARIFF_CONFIDENT_MAX} (исправление)",
        f"tariff: {TARIFF_CONFIDENT_MAX} → {TARIFF_SELF}",
    ]
