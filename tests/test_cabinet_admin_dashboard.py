"""Aggregation correctness for _load_dashboard_data (Фаза 6, п.4 — не было прямых тестов).

/cabinet/admin-panel рендерит cabinet_staff.html из полутора сотен строк
агрегаций (роли, работы по типам, средний балл, «в этом месяце»). Ни один
существующий тест не фиксирует, что эти числа реально считаются правильно.
"""
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM
from app.services.staff_dashboard import REGISTRATION_STATS_SINCE
from app.services.tz import msk_midnight


def _stat_pair(text: str, value, label: str) -> bool:
    """Дашборд рендерит .stat-tile: сначала label в .stat-tile-head, потом value в .stat-tile-value."""
    pattern = re.compile(
        re.escape(label) + r'[^<]*</div>\s*<div class="stat-tile-value">' + re.escape(str(value)) + r'</div>'
    )
    return pattern.search(text) is not None


def test_dashboard_counts_active_and_inactive_users_separately(admin_client, db, user_factory):
    """total_active считает только is_active=True, inactive_count — только False."""
    client, admin = admin_client
    user_factory(vk_id=200_001, name="Student A", role_name="ученик")
    user_factory(vk_id=200_002, name="Student B", role_name="ученик")
    user_factory(vk_id=200_003, name="Curator A", role_name="куратор")
    user_factory(vk_id=200_004, name="Inactive Student", role_name="ученик", is_active=False)

    resp = client.get("/cabinet/admin-panel")
    assert resp.status_code == 200
    text = resp.text

    # admin (rank 5, из фикстуры) + 2 ученика + 1 куратор = 4 активных
    assert _stat_pair(text, 4, "Активных пользователей")
    assert "+ 1 заблокированных" in text


def test_dashboard_works_by_type_and_this_month_counts(admin_client, db):
    """works_by_type/total_works/works_this_month считают по status=success и месяцу."""
    client, admin = admin_client
    now = datetime.now(timezone.utc)
    last_month = now.replace(day=1) - timedelta(days=1)

    db.add_all([
        Work(user_id=admin.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="b1.jpg", status="success", created_at=now),
        Work(user_id=admin.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="b2.jpg", status="success", created_at=now),
        Work(user_id=admin.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="m1.jpg", status="success", created_at=now, score=Decimal("80")),
        Work(user_id=admin.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="m2.jpg", status="success", created_at=now),  # unscored
        # Прошлый месяц — не должен попасть в works_this_month, но должен в total_works.
        Work(user_id=admin.id, work_type=WORK_TYPE_AFTER, month="апрель", year=last_month.year,
             filename="a1.jpg", status="success", created_at=last_month),
        # failed — не должен попасть никуда.
        Work(user_id=admin.id, work_type=WORK_TYPE_BEFORE, month="май", year=now.year,
             filename="failed.jpg", status="failed", created_at=now),
    ])
    db.commit()

    resp = client.get("/cabinet/admin-panel")
    assert resp.status_code == 200
    text = resp.text

    assert "Всего 5 работ" in text  # total_works: 2 before + 2 mock + 1 after (failed исключён)
    assert _stat_pair(text, 4, "Загружено в")  # works_this_month: 2 before + 2 mock, апрель не считается
    assert '<div class="type-val">2</div>' in text  # before
    # avg_score: единственная оценённая работа — 80.
    assert _stat_pair(text, 80, "Средний балл за пробники")


def test_dashboard_unscored_mocks_is_zero_without_active_period(admin_client, db):
    """unscored_mocks не считает работы, если нет активного FeaturePeriod для мок-экзамена.

    Реальных неоценённых пробников в БД — два, но без активного периода
    _load_dashboard_data обязан вернуть 0, а не «все неоценённые за всё время».
    """
    client, admin = admin_client
    now = datetime.now(timezone.utc)
    db.add_all([
        Work(user_id=admin.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="u1.jpg", status="success", created_at=now),
        Work(user_id=admin.id, work_type=WORK_TYPE_MOCK_EXAM, month="май", year=now.year,
             filename="u2.jpg", status="success", created_at=now),
    ])
    db.commit()

    resp = client.get("/cabinet/admin-panel")
    assert resp.status_code == 200
    assert "пробных экзамен" not in resp.text  # блок рендерится только при unscored_mocks > 0


def _registration_counts(text: str) -> dict[str, int]:
    tariff_counts = {
        tariff: int(value)
        for tariff, value in re.findall(
            r'data-registration-tariff="([^"]+)">.*?<div class="type-val">(\d+)</div>',
            text,
            flags=re.S,
        )
    }
    no_tariff = re.search(
        r'data-registration-without-tariff>.*?<div class="type-val">(\d+)</div>',
        text,
        flags=re.S,
    )
    tariff_counts["Без тарифа"] = int(no_tariff.group(1)) if no_tariff else -1
    return tariff_counts


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

    chief_session = session_factory(chief_teacher)
    client.cookies.set("session_id", chief_session.id)
    chief_response = client.get("/cabinet/admin-panel")

    superadmin_session = session_factory(superadmin)
    client.cookies.set("session_id", superadmin_session.id)
    superadmin_response = client.get("/cabinet/superadmin")

    assert chief_response.status_code == 200
    assert superadmin_response.status_code == 200
    assert "Регистрации по тарифам" in chief_response.text
    assert "Учёт с 18.09.2026" in chief_response.text
    assert "Список учеников" in chief_response.text
    assert "@student_self" in chief_response.text
    assert _registration_counts(chief_response.text) == {
        "Я САМ": 1,
        "Я С ВАМИ": 1,
        "УВЕРЕННЫЙ МАКСИМУМ": 1,
        "Без тарифа": 1,
    }
    assert _registration_counts(superadmin_response.text) == _registration_counts(
        chief_response.text
    )
    assert 'href="/cabinet/admin/registration-stats.csv"' in chief_response.text
    assert 'href="/cabinet/superadmin/registration-stats.csv"' in superadmin_response.text

    chief_csv = client.get("/cabinet/admin/registration-stats.csv", cookies={"session_id": chief_session.id})
    superadmin_csv = client.get(
        "/cabinet/superadmin/registration-stats.csv",
        cookies={"session_id": superadmin_session.id},
    )
    assert chief_csv.status_code == 200
    assert superadmin_csv.status_code == 200
    assert chief_csv.headers["content-disposition"] == "attachment; filename=registration-stats.csv"
    assert "Имя;Username;Тариф;Дата регистрации" in chief_csv.content.decode("utf-8-sig")
    assert "@student_self" in superadmin_csv.content.decode("utf-8-sig")
