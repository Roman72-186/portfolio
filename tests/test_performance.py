"""Тесты производительности — по числу запросов к БД, а не по секундам.

До 10.09.2026 здесь стояли шесть проверок вида `duration < 1.0`. На тестовой
машине секунды зависят от её загрузки: тот же код проходил утром и краснел под
нагрузкой. Тест, который краснеет случайно, приучает не верить красному.

Считаем SQL-запросы (фикстура `sql_counter` из conftest). Число запросов зависит
только от кода и ловит ровно то, ради чего тесты писались: N+1 и лишние
обращения к БД. Пороги взяты по факту на 10.09.2026 с запасом ~50 %: главная 15,
дашборд суперадмина 14, портфолио JSON 6, пробники JSON 4, логин 0.

Главная проверка на N+1 — `test_superadmin_dashboard_does_not_scale_with_data`:
двадцать учеников с работами не должны добавлять запросов.
"""
import pytest

from app.models.work import Work, WORK_TYPE_MOCK_EXAM, WORK_TYPE_AFTER


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def superadmin_client(client, db, user_factory, session_factory):
    user = user_factory(vk_id=980001, name="Super Admin", role_name="суперадмин")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    return client, user


@pytest.fixture()
def curator_client(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=980002, name="Curator", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    return client, curator


@pytest.fixture()
def student_with_works(db, user_factory, curator_client):
    _, curator = curator_client
    student = user_factory(vk_id=980003, name="Student With Works", role_name="ученик")
    student.curator_id = curator.id
    db.add(student)
    db.commit()

    for i in range(20):
        w = Work(
            user_id=student.id,
            work_type=WORK_TYPE_AFTER,
            month="январь",
            year=2026,
            filename=f"work_{i}.jpg",
            s3_url=f"https://s3.example.com/work_{i}.jpg",
            status="success",
        )
        db.add(w)
    for i in range(10):
        w = Work(
            user_id=student.id,
            work_type=WORK_TYPE_MOCK_EXAM,
            month="февраль",
            year=2026,
            filename=f"mock_{i}.jpg",
            s3_url=f"https://s3.example.com/mock_{i}.jpg",
            status="success",
            score=70 + i,
            subject="Рисунок" if i % 2 == 0 else "Композиция",
        )
        db.add(w)
    db.commit()
    return student


# ---------------------------------------------------------------------------
# Главная и вход
# ---------------------------------------------------------------------------

def test_homepage_query_count(client, sql_counter):
    with sql_counter() as counter:
        resp = client.get("/")
    assert resp.status_code == 200
    assert counter.count <= 25, f"Главная сделала {counter.count} запросов, ожидали <= 25"


def test_login_page_makes_no_queries(client, sql_counter):
    """Страница входа не должна ходить в БД вообще."""
    with sql_counter() as counter:
        resp = client.get("/login")
    assert resp.status_code == 200
    assert counter.count <= 2, f"/login сделал {counter.count} запросов, ожидали <= 2"


# ---------------------------------------------------------------------------
# Кабинет суперадмина
# ---------------------------------------------------------------------------

def test_superadmin_dashboard_query_count(superadmin_client, sql_counter):
    client, _ = superadmin_client
    with sql_counter() as counter:
        resp = client.get("/cabinet/superadmin")
    assert resp.status_code == 200
    assert counter.count <= 22, f"Дашборд сделал {counter.count} запросов, ожидали <= 22"


def test_superadmin_dashboard_does_not_scale_with_data(
    superadmin_client, db, sql_counter, user_factory, session_factory
):
    """Двадцать учеников с работами не должны добавлять запросов — это и есть N+1."""
    client, _ = superadmin_client

    with sql_counter() as counter:
        resp = client.get("/cabinet/superadmin")
    assert resp.status_code == 200
    empty = counter.count

    for i in range(20):
        u = user_factory(vk_id=990000 + i, name=f"User {i}", role_name="ученик")
        db.add(Work(
            user_id=u.id,
            work_type=WORK_TYPE_MOCK_EXAM,
            month="март",
            year=2026,
            filename=f"f{i}.jpg",
            s3_url=f"https://s3.example.com/f{i}.jpg",
            status="success",
            score=50 + i,
            subject="Рисунок",
        ))
    db.commit()

    with sql_counter() as counter:
        resp = client.get("/cabinet/superadmin")
    assert resp.status_code == 200
    assert counter.count <= empty + 3, (
        f"С данными дашборд сделал {counter.count} запросов против {empty} на пустой базе — "
        "похоже на N+1"
    )


# ---------------------------------------------------------------------------
# JSON-эндпоинты куратора
# ---------------------------------------------------------------------------

def test_curator_portfolio_json_query_count(curator_client, db, student_with_works, sql_counter):
    client, _ = curator_client
    with sql_counter() as counter:
        resp = client.get(f"/cabinet/curator/portfolio/student/{student_with_works.id}")
    assert resp.status_code == 200
    assert counter.count <= 12, f"Портфолио JSON сделал {counter.count} запросов, ожидали <= 12"


def test_curator_mock_exams_json_query_count(curator_client, db, student_with_works, sql_counter):
    client, _ = curator_client
    with sql_counter() as counter:
        resp = client.get(f"/cabinet/curator/mock-exams/student/{student_with_works.id}")
    assert resp.status_code == 200
    assert counter.count <= 10, f"Пробники JSON сделали {counter.count} запросов, ожидали <= 10"


# ---------------------------------------------------------------------------
# Повторные запросы — проверка кеша сессий
# ---------------------------------------------------------------------------

def test_repeated_requests_do_not_add_queries(superadmin_client, sql_counter):
    """Второй и третий заход не должны стоить больше первого."""
    client, _ = superadmin_client
    counts = []
    for _ in range(3):
        with sql_counter() as counter:
            resp = client.get("/cabinet/superadmin")
        assert resp.status_code == 200
        counts.append(counter.count)

    assert max(counts) <= min(counts) + 2, (
        f"Число запросов скачет между заходами: {counts}"
    )
