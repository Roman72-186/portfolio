"""Aggregation correctness for _load_dashboard_data (Фаза 6, п.4 — не было прямых тестов).

/cabinet/admin-panel рендерит cabinet_staff.html из полутора сотен строк
агрегаций (роли, работы по типам, средний балл, «в этом месяце»). Ни один
существующий тест не фиксирует, что эти числа реально считаются правильно.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM
from app.services.staff_dashboard import (
    REGISTRATION_STATS_SINCE,
    get_tariff_registration_stats,
    load_staff_dashboard,
)
from app.services.tz import msk_midnight


def _dashboard(db, admin):
    """Те же данные, что рисует /cabinet/admin-panel (страница проверяется на 200)."""
    return load_staff_dashboard(
        db, {"user_id": admin.id, "role_rank": 4}, datetime.now(timezone.utc)
    )


def test_dashboard_counts_students_not_staff(admin_client, db, user_factory):
    """Плитка «Учеников» считает только учеников: до 28.09.2026 в неё попадали
    куратор и сам суперадмин, а заблокированными считались сотрудники тоже."""
    client, admin = admin_client
    user_factory(vk_id=200_001, name="Student A", role_name="ученик")
    user_factory(vk_id=200_002, name="Student B", role_name="ученик")
    user_factory(vk_id=200_003, name="Curator A", role_name="куратор")
    user_factory(vk_id=200_004, name="Inactive Student", role_name="ученик", is_active=False)
    user_factory(vk_id=200_005, name="Inactive Curator", role_name="куратор", is_active=False)

    assert client.get("/cabinet/admin-panel").status_code == 200
    data = _dashboard(db, admin)

    assert data["students_active"] == 2
    assert data["students_blocked"] == 1


def test_dashboard_works_by_type_and_this_month_counts(admin_client, db, user_factory):
    """works_by_type/total_works/works_this_month считают по status=success и месяцу.

    Работы — ученика: с 07.10.2026 дашборд считает только учеников в учёте
    (`report_scope`), работы сотрудника в цифры не входят."""
    client, admin = admin_client
    student = user_factory(vk_id=200_101, name="Student Works", role_name="ученик")
    now = datetime.now(timezone.utc)
    last_month = now.replace(day=1) - timedelta(days=1)

    db.add_all([
        Work(user_id=student.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="b1.jpg", status="success", created_at=now),
        Work(user_id=student.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="b2.jpg", status="success", created_at=now),
        Work(user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="m1.jpg", status="success", created_at=now, score=Decimal("80")),
        Work(user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="m2.jpg", status="success", created_at=now),  # unscored
        # Прошлый месяц — не должен попасть в works_this_month, но должен в total_works.
        Work(user_id=student.id, work_type=WORK_TYPE_AFTER, month="апрель", year=last_month.year,
             filename="a1.jpg", status="success", created_at=last_month),
        # failed — не должен попасть никуда.
        Work(user_id=student.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="failed.jpg", status="failed", created_at=now),
    ])
    db.commit()

    assert client.get("/cabinet/admin-panel").status_code == 200
    data = _dashboard(db, admin)

    assert data["total_works"] == 5  # 2 before + 2 mock + 1 after, failed исключён
    assert data["works_this_month"] == 4  # апрель не считается
    assert data["works_by_type"][WORK_TYPE_BEFORE] == 2
    assert data["avg_mock_score"] == 80  # единственная оценённая работа


def test_registration_tariff_stats_match_for_chief_teacher_and_superadmin(
    client, db, user_factory, session_factory
):
    cutoff = msk_midnight(REGISTRATION_STATS_SINCE)
    students = [
        user_factory(vk_id=820_001, tariff="Я САМ"),
        user_factory(vk_id=820_002, tariff="Я С ВАМИ"),
        user_factory(vk_id=820_003, tariff="УВЕРЕННЫЙ МАКСИМУМ"),
        user_factory(vk_id=820_004, tariff=""),
    ]
    students[0].tg_username = "student_self"
    for student in students:
        student.created_at = cutoff
    chief_teacher = user_factory(
        vk_id=820_010, name="Главный преподаватель", role_name="админ"
    )
    superadmin = user_factory(
        vk_id=820_011, name="Суперадмин", role_name="суперадмин"
    )
    db.commit()

    # Карточка живёт на «Статистике активности» с 25.09.2026 (f9bdbb9): у ГП
    # и суперадмина одна страница и одни числа.
    chief_session = session_factory(chief_teacher)
    client.cookies.set("session_id", chief_session.id)
    chief_response = client.get("/cabinet/superadmin/activity")

    superadmin_session = session_factory(superadmin)
    client.cookies.set("session_id", superadmin_session.id)
    superadmin_response = client.get("/cabinet/superadmin/activity")

    assert chief_response.status_code == 200
    assert superadmin_response.status_code == 200
    stats = get_tariff_registration_stats(db)
    assert stats["without_tariff"] == 1
    assert stats["total"] == 4

    import io
    import openpyxl

    chief_xlsx = client.get("/cabinet/admin/registration-stats.xlsx", cookies={"session_id": chief_session.id})
    superadmin_xlsx = client.get(
        "/cabinet/superadmin/registration-stats.xlsx",
        cookies={"session_id": superadmin_session.id},
    )
    assert chief_xlsx.status_code == 200
    assert superadmin_xlsx.status_code == 200
    assert chief_xlsx.headers["content-disposition"] == "attachment; filename=registration-stats.xlsx"
    chief_rows = list(openpyxl.load_workbook(io.BytesIO(chief_xlsx.content)).active.values)
    assert chief_rows[0][:4] == ("Фамилия Имя", "Ник Telegram", "Тариф", "Год поступления в вуз")
    superadmin_rows = list(openpyxl.load_workbook(io.BytesIO(superadmin_xlsx.content)).active.values)
    assert "@student_self" in [row[1] for row in superadmin_rows]


# ---------------------------------------------------------------------------
# Разбор дашборда 28.09.2026 (/critique)
# ---------------------------------------------------------------------------

def test_average_score_counts_only_mock_exams(admin_client, db, user_factory):
    """«Средний балл за пробники» раньше брал баллы всех работ подряд."""
    client, admin = admin_client
    student = user_factory(vk_id=200_102, name="Student Score", role_name="ученик")
    now = datetime.now(timezone.utc)
    db.add_all([
        Work(user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="m.jpg", status="success", created_at=now, score=Decimal("60")),
        Work(user_id=student.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="b.jpg", status="success", created_at=now, score=Decimal("100")),
    ])
    db.commit()

    assert _dashboard(db, admin)["avg_mock_score"] == 60


def test_ru_plural_forms():
    from app.tmpl import ru_plural

    forms = ("работа", "работы", "работ")
    assert [ru_plural(n, *forms) for n in (0, 1, 2, 5, 11, 12, 21, 22, 25, 101, 111)] == [
        "работ", "работа", "работы", "работ", "работ", "работ",
        "работа", "работы", "работ", "работа", "работ",
    ]
