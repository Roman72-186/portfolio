"""«Вход в продукт» на «Статистике активности»: новенькие одного месяца прихода.

Владелец 29.10.2026: новенький после адаптации идёт в годовой курс как все,
а в «Входе в продукт» остаётся только за свой месяц прихода. Правило — срез
`report_scope.REPORT_NEWCOMERS`: годовой учёт + тег «Новенькие» + месяц
`program_access_from` по Москве.
"""
from datetime import datetime, timezone

from app.constants import ANNUAL_NEWCOMER_TAG, PROGRAM_HOLIDAY
from app.services.report_scope import (
    REPORT_NEWCOMERS,
    newcomer_cohorts,
    parse_report_program,
    report_program,
    report_program_switch,
    reportable_student_id_set,
)
from app.services.tags import add_tag_to_user, get_or_create_tag


def _newcomer(db, user_factory, *, vk_id, name, arrived_at):
    student = user_factory(vk_id=vk_id, name=name)
    student.program_access_from = arrived_at
    add_tag_to_user(db, student.id, get_or_create_tag(db, ANNUAL_NEWCOMER_TAG).id)
    db.commit()
    return student


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)
    return client


def test_newcomers_scope_is_one_arrival_month(db, user_factory):
    october = _newcomer(db, user_factory, vk_id=330_001, name="Октябрьская Аня",
                        arrived_at=datetime(2026, 10, 25, 9, tzinfo=timezone.utc))
    # 31.10 22:30 UTC — уже 1 ноября по Москве.
    november = _newcomer(db, user_factory, vk_id=330_002, name="Ноябрьский Петя",
                         arrived_at=datetime(2026, 10, 31, 22, 30, tzinfo=timezone.utc))
    old_student = user_factory(vk_id=330_003, name="Старый Ученик")

    with report_program(REPORT_NEWCOMERS, "2026-10"):
        assert reportable_student_id_set(db) == {october.id}
    with report_program(REPORT_NEWCOMERS, "2026-11"):
        assert reportable_student_id_set(db) == {november.id}
    # Без месяца срез пуст, а не «все новенькие».
    with report_program(REPORT_NEWCOMERS):
        assert reportable_student_id_set(db) == set()
    # Годовой курс считает новеньких вместе со всеми.
    assert {october.id, november.id, old_student.id} <= reportable_student_id_set(db)

    assert [(c["value"], c["count"]) for c in newcomer_cohorts(db)] == [("2026-11", 1), ("2026-10", 1)]
    assert newcomer_cohorts(db)[1]["label"] == "Октябрь 2026"


def test_newcomers_scope_follows_annual_rule(db, user_factory):
    arrived = datetime(2026, 10, 26, tzinfo=timezone.utc)
    blocked = _newcomer(db, user_factory, vk_id=330_011, name="Заблокирован", arrived_at=arrived)
    blocked.is_active = False
    holiday = _newcomer(db, user_factory, vk_id=330_012, name="Каникулярный", arrived_at=arrived)
    holiday.program = PROGRAM_HOLIDAY
    db.commit()
    with report_program(REPORT_NEWCOMERS, "2026-10"):
        assert reportable_student_id_set(db) == set()
    assert newcomer_cohorts(db) == []


def test_switch_offers_newcomers_only_where_asked():
    assert parse_report_program(REPORT_NEWCOMERS) == "annual"
    assert parse_report_program(REPORT_NEWCOMERS, newcomers=True) == REPORT_NEWCOMERS
    labels = [o["label"] for o in report_program_switch("/x", "annual")["options"]]
    assert "Вход в продукт" not in labels
    labels = [o["label"] for o in report_program_switch("/x", "annual", newcomers=True)["options"]]
    assert labels[-1] == "Вход в продукт"


def test_activity_page_newcomers_mode(client, db, user_factory, session_factory, admin_user):
    _newcomer(db, user_factory, vk_id=330_021, name="Октябрьская Новенькая",
              arrived_at=datetime(2026, 10, 25, 9, tzinfo=timezone.utc))
    _newcomer(db, user_factory, vk_id=330_022, name="Ноябрьский Новенький",
              arrived_at=datetime(2026, 11, 2, 9, tzinfo=timezone.utc))
    user_factory(vk_id=330_023, name="Годовой Старичок")
    client = _login(client, session_factory, admin_user)

    # По умолчанию — последний месяц прихода.
    page = client.get(f"/cabinet/superadmin/activity?program={REPORT_NEWCOMERS}")
    assert page.status_code == 200
    assert "Ноябрьский Новенький" in page.text
    assert "Октябрьская Новенькая" not in page.text and "Годовой Старичок" not in page.text
    assert 'aria-label="Месяц прихода"' in page.text
    # Отчёт месяца и регистрации считают всю школу — в срезе их нет.
    assert "data-tariff-flow" not in page.text

    page = client.get(f"/cabinet/superadmin/activity?program={REPORT_NEWCOMERS}&cohort=2026-10").text
    assert "Октябрьская Новенькая" in page and "Ноябрьский Новенький" not in page

    annual = client.get("/cabinet/superadmin/activity").text
    assert "Годовой Старичок" in annual and "Октябрьская Новенькая" in annual
    assert "Вход в продукт" in annual


def test_activity_page_newcomers_empty(client, db, session_factory, admin_user):
    client = _login(client, session_factory, admin_user)
    page = client.get(f"/cabinet/superadmin/activity?program={REPORT_NEWCOMERS}")
    assert page.status_code == 200
    assert "Новеньких пока нет" in page.text
