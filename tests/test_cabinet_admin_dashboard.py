"""Aggregation correctness for _load_dashboard_data (Фаза 6, п.4 — не было прямых тестов).

/cabinet/admin-panel рендерит cabinet_staff.html из полутора сотен строк
агрегаций (роли, работы по типам, средний балл, «в этом месяце»). Ни один
существующий тест не фиксирует, что эти числа реально считаются правильно.
"""
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.work import Work, WORK_TYPE_BEFORE, WORK_TYPE_AFTER, WORK_TYPE_MOCK_EXAM


def _stat_pair(text: str, value, label: str) -> bool:
    pattern = re.compile(
        r'stat-val">' + re.escape(str(value)) + r'</div>\s*<div class="stat-lbl">' + re.escape(label)
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
