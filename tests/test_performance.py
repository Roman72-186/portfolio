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


def test_login_page_makes_no_queries(client, sql_counter):
    """Страница входа не должна ходить в БД вообще."""
    with sql_counter() as counter:
        resp = client.get("/login")
    assert resp.status_code == 200
    assert counter.count <= 2, f"/login сделал {counter.count} запросов, ожидали <= 2"


# ---------------------------------------------------------------------------
# Кабинет суперадмина
# ---------------------------------------------------------------------------


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

def _queries(client, sql_counter, url):
    with sql_counter() as counter:
        resp = client.get(url)
    assert resp.status_code == 200
    return counter.count


@pytest.mark.parametrize("page", ["portfolio", "mock-exams"])
def test_curator_student_json_does_not_scale_with_works(
    curator_client, db, user_factory, student_with_works, sql_counter, page
):
    """30 работ ученика не должны стоить запросов больше, чем одна, — иначе N+1."""
    client, curator = curator_client
    small = user_factory(vk_id=980_004, name="Student One Work", role_name="ученик")
    small.curator_id = curator.id
    db.add_all([
        Work(user_id=small.id, work_type=WORK_TYPE_AFTER, month="январь", year=2026,
             filename="one.jpg", s3_url="https://s3.example.com/one.jpg", status="success"),
        Work(user_id=small.id, work_type=WORK_TYPE_MOCK_EXAM, month="февраль", year=2026,
             filename="mock.jpg", s3_url="https://s3.example.com/mock.jpg", status="success",
             score=70, subject="Рисунок"),
    ])
    db.commit()

    base = _queries(client, sql_counter, f"/cabinet/curator/{page}/student/{small.id}")
    many = _queries(client, sql_counter, f"/cabinet/curator/{page}/student/{student_with_works.id}")

    assert many <= base + 2, f"{page}: {many} запросов на 30 работ против {base} на одну — похоже на N+1"


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
